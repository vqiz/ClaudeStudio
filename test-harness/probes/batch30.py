#!/usr/bin/env python3
"""Verifikation Build-Batch 30 (echter Core, echte Subprozess-Orchestrierung, Stub-`claude`):

  F117  Agent-Lauf streamt Live-Output: session.start spawnt einen echten Subprozess, dessen
        stream-json zeilenweise geparst und als session.event-Frames live (inkrementell über die
        Zeit) an den Client geschickt wird.
  F118  Agent mitten im Lauf stoppen: session.stop killt den claude-Subprozess, der Stream endet
        mit 'stopped' weit vor dem regulären Ende, der Subprozess ist danach beendet.

Der Stub-`claude` (test-harness/lib/stub_claude.sh) emittiert das echte stream-json-Protokoll —
getestet wird die App-Orchestrierung (Spawn/Stream/Kill), nicht das LLM.
"""
from __future__ import annotations
import json, subprocess, sys, tempfile, time, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
STUB = ROOT / "test-harness" / "lib" / "stub_claude.sh"
PIDFILE = Path(tempfile.gettempdir()) / "cs_b30_stub.pid"
results: dict[str, dict] = {}


def proc_alive(pid: int) -> bool:
    return subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode == 0


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def start_session(c: P.Client, prompt: str, cwd: str):
    """Sendet session.start, liest den Ack, liefert (session_id, request_id)."""
    rid = str(uuid.uuid4())
    env = {"id": rid, "kind": "request", "method": "session.start",
           "payload": {"prompt": prompt, "cwd": cwd, "binary": str(STUB)}}
    c.sock.sendall(P.encode_frame(env))
    while True:
        f = c._read_frame()
        if f.get("id") == rid and f.get("kind") != "event":
            return (f.get("payload") or {}).get("session_id"), rid


def read_events(c: P.Client, until_kinds, max_wait=15.0):
    """Liest session.event-Frames, bis ein Event-kind aus until_kinds kommt.
    Liefert Liste von (timestamp, event_dict)."""
    out = []
    deadline = time.time() + max_wait
    while time.time() < deadline:
        f = c._read_frame()
        if f.get("method") == "session.event" or f.get("kind") == "event":
            evd = (f.get("payload") or {}).get("event") or {}
            out.append((time.time(), evd))
            if evd.get("kind") in until_kinds:
                break
    return out


def main():
    PIDFILE.unlink(missing_ok=True)
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b30.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": str(STUB),
                                   "CS_STUB_PIDFILE": str(PIDFILE)}) as ctx:
        sock = ctx["sock"]

        # F117 — Live-Output streamt inkrementell
        try:
            c = P.Client(sock, timeout=15)
            sid, _ = start_session(c, "Implementiere ein Health-Endpoint", str(ROOT))
            assert sid
            events = read_events(c, until_kinds={"result"}, max_wait=15)
            texts = [(t, e) for (t, e) in events if e.get("kind") == "assistant_text"]
            kinds = [e.get("kind") for (_, e) in events]
            assert len(texts) >= 3, f"nur {len(texts)} Text-Events: {kinds}"
            span = texts[-1][0] - texts[0][0]
            assert span > 0.3, f"Output kam nicht inkrementell (span={span:.2f}s)"
            assert "result" in kinds
            c.close()
            record("F117", "pass", ev("F117", "live-stream.json",
                   {"session_id": sid, "event_kinds": kinds,
                    "text_events": len(texts), "stream_span_s": round(span, 2)}),
                   f"{len(texts)} Live-Text-Events über {span:.2f}s gestreamt + result")
        except Exception as e:
            record("F117", "fail", note=str(e))

        # F118 — Stop killt den Subprozess mitten im Lauf
        try:
            ca = P.Client(sock, timeout=15)   # Lauf-Verbindung
            cb = P.Client(sock, timeout=15)   # Steuer-Verbindung (stop)
            PIDFILE.unlink(missing_ok=True)
            sid, _ = start_session(ca, "Starte LONGRUN Aufgabe", str(ROOT))
            assert sid
            # bis zum ersten Live-Text lesen (danach schläft der Stub 10s)
            first = read_events(ca, until_kinds={"assistant_text"}, max_wait=6)
            assert any(e.get("kind") == "assistant_text" for (_, e) in first)
            # exakte Subprozess-PID aus dem Pidfile (über exec hinweg stabil)
            for _ in range(20):
                if PIDFILE.exists():
                    break
                time.sleep(0.05)
            pid = int(PIDFILE.read_text().strip())
            assert proc_alive(pid), f"Stub-Subprozess {pid} läuft nicht"
            t0 = time.time()
            stop = cb.request("session.stop", {"session_id": sid})
            assert stop["ok"] and stop["stopped"] is True
            # der Stream muss zügig mit 'stopped' enden (<< 10s Stub-Sleep)
            tail = read_events(ca, until_kinds={"stopped", "result"}, max_wait=8)
            elapsed = time.time() - t0
            got_stopped = any(e.get("kind") == "stopped" for (_, e) in tail)
            assert got_stopped, f"kein 'stopped'-Event: {[e.get('kind') for _, e in tail]}"
            assert elapsed < 5, f"Stop dauerte {elapsed:.1f}s"
            time.sleep(0.5)
            assert not proc_alive(pid), f"Subprozess {pid} noch aktiv"
            ca.close(); cb.close()
            record("F118", "pass", ev("F118", "stop-kill.json",
                   {"session_id": sid, "stop_result": stop, "stopped_event": got_stopped,
                    "elapsed_to_stop_s": round(elapsed, 2), "subprocess_gone": True}),
                   f"session.stop killte den Subprozess; 'stopped' nach {elapsed:.2f}s, Prozess beendet")
        except Exception as e:
            record("F118", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
