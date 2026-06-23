#!/usr/bin/env python3
"""Verifikation Build-Batch 45 (echter Core, Stub-`claude`, kein Mock):

  F349  Live-Session-Sharing: Client A teilt eine laufende Session per Token/Link; Client B
        löst das Token auf (session.join) und liest die Session live mit (session.messages),
        WÄHREND A's Lauf noch läuft. Ungültiges Token wird abgelehnt.
"""
from __future__ import annotations
import json, sys, tempfile, time, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
STUB = ROOT / "test-harness" / "lib" / "stub_claude.sh"
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b45.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": str(STUB)}) as ctx:
        sock = ctx["sock"]
        ca = P.Client(sock, timeout=15)   # Client A (führt die Session)
        cb = P.Client(sock, timeout=15)   # Client B (liest mit)
        ctrl = P.Client(sock, timeout=15)
        try:
            # A startet eine LONGRUN-Session (läuft, schläft nach Schritt 1)
            rid = str(uuid.uuid4())
            cwd = tempfile.mkdtemp(prefix="cs-b45-")
            ca.sock.sendall(P.encode_frame({"id": rid, "kind": "request", "method": "session.start",
                "payload": {"prompt": "LONGRUN Feature bauen", "cwd": cwd, "binary": str(STUB)}}))
            sid = None
            while True:
                f = ca._read_frame()
                if f.get("id") == rid and f.get("kind") != "event":
                    sid = (f.get("payload") or {}).get("session_id"); continue
                if ((f.get("payload") or {}).get("event") or {}).get("kind") == "assistant_text":
                    break  # Schritt 1 ist jetzt in der DB; A schläft noch
            assert sid

            # A teilt die laufende Session -> Token/Link
            share = ctrl.request("session.share", {"session_id": sid})
            token = share["token"]
            assert share["link"].endswith(token)

            # B löst das Token auf und liest live mit (A läuft noch)
            joined = cb.request("session.join", {"token": token})
            assert joined["session_id"] == sid
            # kurz warten, falls die Nachricht noch eingebettet wird (record_message ist async)
            msgs = []
            for _ in range(30):
                msgs = cb.request("session.messages", {"id": sid})["messages"]
                if any("Schritt 1" in (m.get("content") or "") for m in msgs):
                    break
                time.sleep(0.1)
            roles = [m.get("role") for m in msgs]
            assert "user" in roles and any("Schritt 1" in (m.get("content") or "") for m in msgs), roles
            # B kennt sid NUR über das Token -> echtes Mitlesen via Share-Link

            # ungültiges Token wird abgelehnt
            bad = False
            try:
                cb.request("session.join", {"token": "share-nonexistent"})
            except P.RemoteError:
                bad = True
            assert bad

            ctrl.request("session.stop", {"session_id": sid})   # A-Lauf beenden
            record("F349", "pass", ev("F349", "session-share.json",
                   {"shared_session": sid, "token": token, "link": share["link"],
                    "client_b_saw_messages": len(msgs), "client_b_roles": roles}),
                   f"Client B las die geteilte Session live mit ({len(msgs)} Nachrichten) via Token")
        except Exception as e:
            record("F349", "fail", note=str(e))
        finally:
            ca.close(); cb.close(); ctrl.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
