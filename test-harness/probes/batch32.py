#!/usr/bin/env python3
"""Verifikation Build-Batch 32 (echter Core, Stub-`claude`, kein Mock):

  F163  'Ab hier weitermachen' startet eine neue Session, die den vorherigen Lauf per --resume
        fortsetzt (der gespawnte Prozess erhält die Claude-Session-Id des Erstlaufs).
  F300  Supervisor führt laufende Agenten als 'observed'; zwei parallele Agenten -> running=2,
        nach Stop eines Agenten -> running=1 (lebende Agenten-Registry).
"""
from __future__ import annotations
import json, sys, time, uuid
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


def start(c: P.Client, prompt: str, resume: str | None = None):
    rid = str(uuid.uuid4())
    payload = {"prompt": prompt, "cwd": str(ROOT), "binary": str(STUB)}
    if resume:
        payload["resume"] = resume
    c.sock.sendall(P.encode_frame({"id": rid, "kind": "request",
                                   "method": "session.start", "payload": payload}))
    while True:
        f = c._read_frame()
        if f.get("id") == rid and f.get("kind") != "event":
            return (f.get("payload") or {}).get("session_id")


def drain(c: P.Client, until_kinds, max_wait=15.0):
    out = []
    deadline = time.time() + max_wait
    while time.time() < deadline:
        f = c._read_frame()
        if f.get("method") == "session.event" or f.get("kind") == "event":
            evd = (f.get("payload") or {}).get("event") or {}
            out.append(evd)
            if evd.get("kind") in until_kinds:
                break
    return out


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b32.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": str(STUB)}) as ctx:
        sock = ctx["sock"]

        # F163 — 'Ab hier weitermachen' setzt den vorherigen Lauf per --resume fort
        try:
            c = P.Client(sock, timeout=15)
            sid1 = start(c, "Baue das Login-Formular")
            ev1 = drain(c, {"result"})
            claude_sid = next(e["session_id"] for e in ev1 if e.get("kind") == "claude_session")
            assert claude_sid
            # neue Session, die den vorherigen Claude-Lauf fortsetzt
            sid2 = start(c, "Mach mit der vorhin genannten Datei weiter", resume=claude_sid)
            assert sid2 and sid2 != sid1
            ev2 = drain(c, {"result"})
            texts = [e.get("text", "") for e in ev2 if e.get("kind") == "assistant_text"]
            resumed = next((t for t in texts if t.startswith("RESUMED:")), "")
            assert resumed == f"RESUMED:{claude_sid}", f"{resumed!r} != RESUMED:{claude_sid}"
            c.close()
            record("F163", "pass", ev("F163", "resume.json",
                   {"first_session": sid1, "claude_session": claude_sid,
                    "continued_session": sid2, "resume_marker": resumed}),
                   f"Folge-Session {sid2[:8]} setzt Claude-Session {claude_sid} per --resume fort")
        except Exception as e:
            record("F163", "fail", note=str(e))

        # F300 — Supervisor führt laufende Agenten; Stop entfernt einen
        try:
            ca = P.Client(sock, timeout=15)
            cb = P.Client(sock, timeout=15)
            ctrl = P.Client(sock, timeout=15)
            sid_a = start(ca, "LONGRUN Agent A")
            drain(ca, {"assistant_text"}, max_wait=6)   # läuft jetzt (schläft)
            sid_b = start(cb, "LONGRUN Agent B")
            drain(cb, {"assistant_text"}, max_wait=6)
            ra = ctrl.request("os.running_agents", {})
            assert ra["count"] == 2 and set(ra["running"]) == {sid_a, sid_b}
            assert ra["supervisor"]["alive"] and ra["supervisor"]["observed"] == 2
            # einen Agenten stoppen -> Registry schrumpft auf 1
            ctrl.request("session.stop", {"session_id": sid_a})
            after = None
            for _ in range(40):
                time.sleep(0.1)
                after = ctrl.request("os.running_agents", {})
                if after["count"] == 1:
                    break
            assert after["count"] == 1 and after["running"] == [sid_b]
            assert after["supervisor"]["observed"] == 1
            ca.close(); cb.close(); ctrl.close()
            record("F300", "pass", ev("F300", "supervisor.json",
                   {"both_running": ra, "after_stop_one": after}),
                   "Supervisor beobachtete 2 laufende Agenten; nach Stop verbleibt 1")
        except Exception as e:
            record("F300", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
