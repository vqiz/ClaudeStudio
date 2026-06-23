#!/usr/bin/env python3
"""Verifikation Build-Batch 19: Session-Events/Errors, Task-Output-Typ, Worktime-Export.
Neu (router.rs): session.record_event/events/record_error, tasks.deliver_output, worktime.export.
cs-sessions: list_events. Echter Core + echte sessions.db. Kein Mock.
"""
from __future__ import annotations
import json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b19.log")) as ctx:
        c = P.Client(ctx["sock"])

        # F155 permission/hook/mcp events stored with timestamp
        try:
            sid = c.request("session.create", {"title": "evented", "cwd": str(ROOT)})["id"]
            for kind, pl in [("permission", {"tool": "Bash", "decision": "ask"}),
                             ("hook", {"event": "PostToolUse", "matcher": "Edit"}),
                             ("mcp", {"server": "github", "tool": "create_issue"})]:
                c.request("session.record_event", {"session_id": sid, "kind": kind, "payload": pl})
            events = c.request("session.events", {"session_id": sid})["events"]
            kinds = {e["kind"] for e in events}
            assert {"permission", "hook", "mcp"} <= kinds
            assert all(e.get("created_at", 0) > 0 for e in events)
            record("F155", "pass", ev("F155", "events.json", events), "permission/hook/mcp-Events mit Zeitstempel gespeichert")
        except Exception as e:
            record("F155", "fail", note=str(e))

        # F156 error + numbered retries with exit-code
        try:
            sid = c.request("session.create", {"title": "errs", "cwd": str(ROOT)})["id"]
            for n in range(3):
                c.request("session.record_error", {"session_id": sid, "error": "cat: nichtvorhanden.txt: No such file",
                                                   "exit_code": 1, "retry": n})
            errs = c.request("session.events", {"session_id": sid, "kind": "error"})["events"]
            retries = sorted(e["payload"]["retry"] for e in errs)
            assert len(errs) == 3 and retries == [0, 1, 2]
            assert all(e["payload"]["exit_code"] == 1 and "No such file" in e["payload"]["error"] for e in errs)
            record("F156", "pass", ev("F156", "errors.json", errs), "Fehler+exit-code + nummerierte Retries gespeichert")
        except Exception as e:
            record("F156", "fail", note=str(e))

        # F212 output type: Datei creates a file, Report returns a report
        try:
            out = Path(tempfile.mkdtemp(prefix="cs-b19-out-")) / "report.md"
            f = c.request("tasks.deliver_output", {"type": "Datei", "path": str(out), "content": "scan results: 0 issues"})
            assert f["result"] == "file" and out.exists() and "scan results" in out.read_text()
            r = c.request("tasks.deliver_output", {"type": "Report", "content": "scan results: 0 issues"})
            assert r["result"] == "report" and "Task-Report" in r["report"]
            record("F212", "pass", ev("F212", "output.json", {"file": f, "report": r}),
                   "Output-Typ Datei->Datei, Report->Report")
        except Exception as e:
            record("F212", "fail", note=str(e))

        # F356 worktime tracking -> Toggl export
        try:
            sid = c.request("session.create", {"title": "tracked work", "cwd": str(ROOT)})["id"]
            c.request("session.record_event", {"session_id": sid, "kind": "stopped", "payload": {}})
            r = c.request("worktime.export", {})
            assert r["format"] == "toggl" and r["entries"]
            entry = next(e for e in r["entries"] if e.get("description") == "tracked work")
            assert "start_ms" in entry and "stop_ms" in entry and "duration_seconds" in entry
            record("F356", "pass", ev("F356", "worktime.json", {"sample": entry, "total_seconds": r["total_seconds"]}),
                   "aktive Session-Zeit als Toggl-kompatible Einträge exportiert")
        except Exception as e:
            record("F356", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
