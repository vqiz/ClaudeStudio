#!/usr/bin/env python3
"""Verifikation Build-Batch 104 (echter Core, Stub-`claude`, echtes SIGSTOP/SIGCONT, kein Mock):

  F139  Pause hält eine laufende Session SOFORT an, OHNE den Subprozess zu beenden: session.pause
        sendet SIGSTOP → der Prozess ist angehalten (Zustand 'T'), bleibt aber am Leben; session.resume
        (SIGCONT) setzt ihn fort.
  F140  Während einer Pause kann der User per session.inject eine Zusatznachricht in die Session
        einbringen (in die Warteschlange, als Event protokolliert).
"""
from __future__ import annotations
import json, subprocess, sys, time, uuid
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


def proc_state(pid):
    r = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return r.stdout.strip()


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b82.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": str(STUB)}) as ctx:
        sock = ctx["sock"]; home = ctx["home"]
        ca = P.Client(sock, timeout=15)
        ctrl = P.Client(sock, timeout=15)
        try:
            rid = str(uuid.uuid4())
            ca.sock.sendall(P.encode_frame({"id": rid, "kind": "request", "method": "session.start",
                "payload": {"prompt": "LONGRUN lange Aufgabe", "cwd": str(ROOT), "binary": str(STUB)}}))
            sid, spawned_pid = None, None
            while True:
                f = ca._read_frame()
                if f.get("id") == rid and f.get("kind") != "event":
                    sid = (f.get("payload") or {}).get("session_id"); continue
                evd = ((f.get("payload") or {}).get("event") or {})
                if evd.get("kind") == "spawned":
                    spawned_pid = evd.get("pid")
                if evd.get("kind") == "assistant_text":
                    break
            assert sid, "keine session_id"
            time.sleep(0.4)

            # ---- F139: Pause (SIGSTOP) ----
            pause = ctrl.request("session.pause", {"session_id": sid})
            assert pause["paused"] is True, pause
            pid = pause["pid"]
            time.sleep(0.4)
            st = proc_state(pid)
            assert st, f"Subprozess (PID {pid}) NICHT mehr am Leben — wurde beendet statt pausiert"
            assert "T" in st, f"Prozess nicht angehalten (Zustand {st!r})"

            # ---- F140: Einwerfen während der Pause ----
            inj = ctrl.request("session.inject",
                               {"session_id": sid, "message": "Bitte zusätzlich die Tests ausführen."})
            assert inj["injected"] is True and inj["during_pause"] is True, inj

            # ---- F139: Resume (SIGCONT) ----
            resume = ctrl.request("session.resume", {"session_id": sid})
            assert resume["resumed"] is True, resume
            time.sleep(0.4)
            st2 = proc_state(pid)
            assert st2 and "T" not in st2, f"Prozess nach Resume noch angehalten ({st2!r})"

            # Event-Log enthält die eingeworfene Nachricht
            logs = list(Path(home).glob("**/event_log.jsonl"))
            entries = [json.loads(l) for lf in logs for l in lf.read_text().splitlines() if l.strip()] if logs else []
            injected = [e for e in entries if e.get("kind") == "voice_injected_message"]
            assert injected, "eingeworfene Nachricht nicht im Event-Log"

            ctrl.request("session.stop", {"session_id": sid})
            record("F139", "pass", ev("F139", "pause-resume.json",
                   {"pid": pid, "spawned_pid": spawned_pid, "state_after_pause": st,
                    "state_after_resume": st2, "pause": pause, "resume": resume}),
                   f"Pause→SIGSTOP: Prozess PID {pid} angehalten (Zustand {st}) & am Leben; Resume→SIGCONT (Zustand {st2})")
            record("F140", "pass", ev("F140", "inject.json",
                   {"inject": inj, "event_log_entry": injected[-1]}),
                   "Einwerfen während Pause: Nachricht in die Session-Warteschlange + ins Event-Log geschrieben")
        except Exception as e:
            record("F139", "fail", note=str(e)); record("F140", "fail", note=str(e))
        finally:
            ca.close(); ctrl.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
