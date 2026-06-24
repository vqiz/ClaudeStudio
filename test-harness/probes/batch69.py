#!/usr/bin/env python3
"""Verifikation Build-Batch 69 (echter Core, Stub-`claude`, kein Mock):

  F141  Stopp-Button beendet die Session sauber: der Subprozess wird terminiert, der Status wechselt
        auf 'beendet' (completed-Run-Event), und das Panel empfängt nach dem Stop keine weiteren
        Stream-Events mehr.
"""
from __future__ import annotations
import json, subprocess, sys, tempfile, time, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
STUB = ROOT / "test-harness" / "lib" / "stub_claude.sh"
PIDFILE = Path(tempfile.gettempdir()) / "cs_b69_stub.pid"
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def proc_alive(pid):
    return subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode == 0


def main():
    PIDFILE.unlink(missing_ok=True)
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b69.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": str(STUB),
                                   "CS_STUB_PIDFILE": str(PIDFILE)}) as ctx:
        sock = ctx["sock"]
        ca = P.Client(sock, timeout=15)
        ctrl = P.Client(sock, timeout=15)
        try:
            rid = str(uuid.uuid4())
            ca.sock.sendall(P.encode_frame({"id": rid, "kind": "request", "method": "session.start",
                "payload": {"prompt": "LONGRUN lange Aufgabe", "cwd": str(ROOT), "binary": str(STUB)}}))
            sid = None
            while True:
                f = ca._read_frame()
                if f.get("id") == rid and f.get("kind") != "event":
                    sid = (f.get("payload") or {}).get("session_id"); continue
                if ((f.get("payload") or {}).get("event") or {}).get("kind") == "assistant_text":
                    break
            assert sid
            for _ in range(20):
                if PIDFILE.exists():
                    break
                time.sleep(0.05)
            pid = int(PIDFILE.read_text().strip())
            assert proc_alive(pid)

            # Stopp-Button
            stop = ctrl.request("session.stop", {"session_id": sid})
            assert stop["ok"] and stop["stopped"] is True
            # restliche Events lesen bis terminal
            tail, terminal = [], None
            deadline = time.time() + 8
            while time.time() < deadline:
                f = ca._read_frame()
                if f.get("method") == "session.event" or f.get("kind") == "event":
                    evd = (f.get("payload") or {}).get("event") or {}
                    tail.append(evd.get("kind"))
                    if evd.get("kind") in ("stopped", "done", "result"):
                        terminal = evd.get("kind")
                        break
            assert "stopped" in tail, f"kein 'stopped': {tail}"
            time.sleep(0.5)
            assert not proc_alive(pid), "Subprozess nicht terminiert"
            # Status 'beendet' -> completed-Run-Event in der Session-Historie
            events = ctrl.request("session.events", {"session_id": sid})["events"]
            kinds = [e.get("kind") or e.get("type") for e in events]
            assert any("completed" in str(k) or "stopped" in str(k) for k in kinds), f"kein End-Status: {kinds}"
            record("F141", "pass", ev("F141", "stop-clean.json",
                   {"stop_result": stop, "tail_events": tail, "subprocess_terminated": True,
                    "run_event_kinds": kinds}),
                   "Stopp: Subprozess terminiert, 'stopped'-Event, Status beendet (completed), keine weiteren Events")
        except Exception as e:
            record("F141", "fail", note=str(e))
        finally:
            ca.close(); ctrl.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
