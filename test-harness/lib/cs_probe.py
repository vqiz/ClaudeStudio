#!/usr/bin/env python3
"""cs_probe — echte IPC-Verifikations-Bibliothek für den ClaudeStudio-Build-Loop.

Spricht das echte length-prefixed-MessagePack-Protokoll des Rust-Cores (siehe
core/crates/cs-ipc): 4 Byte big-endian Länge + msgpack-Map mit den Feldern
{id, kind, method, payload}. kind ist lowercase ("request"/"response"/"event"/"error").

Dieses Modul ist die Grundlage, mit der jedes core-gestützte Feature im ECHTEN
Betrieb getestet wird: es startet den echten Core-Prozess, schickt echte
Requests über den echten Socket, liest echte Responses und schreibt das
Ergebnis als Evidence nach test-harness/evidence/<FEATURE-ID>/.

Kein Mock, kein Stub — der reale Core beantwortet jede Anfrage.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import msgpack

ROOT = Path(__file__).resolve().parents[2]
CORE_BIN = ROOT / "core" / "target" / "debug" / "claudestudio-core"
EVIDENCE = ROOT / "test-harness" / "evidence"
FEATURE_LIST = ROOT / "feature_list.json"

MAX_FRAME = 16 * 1024 * 1024


# ---------------------------------------------------------------------------
# Wire-Codec
# ---------------------------------------------------------------------------
def encode_frame(obj: dict) -> bytes:
    body = msgpack.packb(obj, use_bin_type=True)
    if len(body) > MAX_FRAME:
        raise ValueError("frame too large")
    return struct.pack(">I", len(body)) + body


class Client:
    """Synchroner IPC-Client gegen den laufenden Core-Socket."""

    def __init__(self, sock_path: Path, timeout: float = 15.0):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(str(sock_path))
        self._buf = b""

    def _read_exactly(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("core closed connection")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _read_frame(self) -> dict:
        (length,) = struct.unpack(">I", self._read_exactly(4))
        if length > MAX_FRAME:
            raise ValueError("frame too large")
        body = self._read_exactly(length)
        return msgpack.unpackb(body, raw=False)

    def request(self, method: str, payload: dict | None = None) -> dict:
        """Sendet eine Request und liefert das Response-Payload zurück.

        Events (kind=="event") mit anderer id werden übersprungen. Ein
        kind=="error" wird als RemoteError geworfen.
        """
        rid = str(uuid.uuid4())
        env = {"id": rid, "kind": "request", "method": method, "payload": payload or {}}
        self.sock.sendall(encode_frame(env))
        deadline = time.time() + self.sock.gettimeout()
        while True:
            if time.time() > deadline:
                raise TimeoutError(f"no response for {method}")
            frame = self._read_frame()
            if frame.get("id") != rid:
                # fremdes Event/Frame — ignorieren
                continue
            kind = frame.get("kind")
            if kind == "error":
                p = frame.get("payload") or {}
                raise RemoteError(p.get("code"), p.get("message"), method)
            return frame.get("payload")

    def subscribe_events(self):
        """Sendet events.subscribe und liefert einen Generator über Event-Frames."""
        rid = str(uuid.uuid4())
        env = {"id": rid, "kind": "request", "method": "events.subscribe", "payload": {}}
        self.sock.sendall(encode_frame(env))

        def gen():
            while True:
                frame = self._read_frame()
                if frame.get("kind") == "event":
                    yield frame
        return gen()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class RemoteError(Exception):
    def __init__(self, code, message, method):
        super().__init__(f"[{code}] {message} (method={method})")
        self.code = code
        self.message = message
        self.method = method


# ---------------------------------------------------------------------------
# Core-Launcher
# ---------------------------------------------------------------------------
@contextmanager
def running_core(home: Path | None = None, library_dir: Path | None = None,
                 log_path: Path | None = None, env_extra: dict | None = None,
                 seed_models: bool = True):
    """Startet den echten Core mit eigenem Socket; räumt am Ende auf.

    home=None  → frisches Temp-HOME (isolierter State, sauberes sessions.db).
    home=Path  → dieses HOME nutzen (z.B. das echte, wenn claude-Login nötig).

    seed_models=True verlinkt den bereits heruntergeladenen Modell-Cache aus dem
    echten ~/.claudestudio/models ins Temp-HOME, damit der Core beim Start NICHT
    erneut 90 MB lädt (sonst blockieren semantische Operationen / Hänger).
    """
    import tempfile
    tmp = None
    if home is None:
        tmp = tempfile.mkdtemp(prefix="cs-probe-home-")
        home = Path(tmp)
    # Modell-Cache aus dem echten HOME ins Temp-HOME spiegeln (Symlink, instant).
    if seed_models and tmp is not None:
        real_models = Path(os.path.expanduser("~/.claudestudio/models"))
        if real_models.is_dir():
            (Path(home) / ".claudestudio").mkdir(parents=True, exist_ok=True)
            link = Path(home) / ".claudestudio" / "models"
            if not link.exists():
                try:
                    link.symlink_to(real_models)
                except OSError:
                    pass
    sock_path = Path(home) / "core.sock"
    if sock_path.exists():
        sock_path.unlink()

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["RUST_LOG"] = env.get("RUST_LOG", "info")
    env.pop("CLAUDESTUDIO_WATCH_STDIN", None)
    if library_dir is not None:
        env["CLAUDESTUDIO_LIBRARY_DIR"] = str(library_dir)
    if env_extra:
        env.update(env_extra)

    if not CORE_BIN.exists():
        raise FileNotFoundError(f"core binary missing: {CORE_BIN} — run `cargo build -p cs-cli`")

    logf = open(log_path, "w") if log_path else subprocess.DEVNULL
    proc = subprocess.Popen([str(CORE_BIN), str(sock_path)], env=env,
                            stdout=logf, stderr=subprocess.STDOUT)
    try:
        # auf Socket warten (max 10s)
        for _ in range(100):
            if sock_path.exists():
                break
            if proc.poll() is not None:
                raise RuntimeError(f"core exited early (code {proc.returncode}) — see {log_path}")
            time.sleep(0.1)
        if not sock_path.exists():
            raise TimeoutError("core socket did not appear within 10s")
        # kleiner Puffer, damit der Accept-Loop sicher läuft
        time.sleep(0.2)
        yield {"home": Path(home), "sock": sock_path, "proc": proc}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if logf not in (subprocess.DEVNULL,):
            logf.close()
        if tmp:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Evidence + feature_list
# ---------------------------------------------------------------------------
def evidence_dir(fid: str) -> Path:
    d = EVIDENCE / fid
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_evidence(fid: str, name: str, content: str) -> Path:
    d = evidence_dir(fid)
    p = d / name
    p.write_text(content)
    return p


def load_features() -> list:
    return json.loads(FEATURE_LIST.read_text())


def mark_passing(fids: list[str]) -> int:
    """Setzt passes=true für die gegebenen IDs (nur dieses Feld)."""
    feats = load_features()
    by_id = {f["id"]: f for f in feats}
    n = 0
    for fid in fids:
        if fid in by_id and not by_id[fid]["passes"]:
            by_id[fid]["passes"] = True
            n += 1
    FEATURE_LIST.write_text(json.dumps(feats, ensure_ascii=False, indent=2) + "\n")
    return n


if __name__ == "__main__":
    # Selbsttest: Core starten, ping schicken.
    print("self-test: launching core + ping…")
    with running_core(log_path=Path("/tmp/cs-probe-selftest.log")) as ctx:
        c = Client(ctx["sock"])
        try:
            r = c.request("ping", {})
            print("ping ->", json.dumps(r))
        finally:
            c.close()
    print("OK")
