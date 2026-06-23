#!/usr/bin/env python3
"""Verifikation Build-Batch 13: A2A/Teams, Task-Render, DB-Migration, API-Portal,
Snapshots, A11y, Session-Kommentare — gegen den echten Core. File/Logik, kein Mock.
"""
from __future__ import annotations
import json, sys
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
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b13.log")) as ctx:
        c = P.Client(ctx["sock"])

        # F125 / F127: A2A message flows and arrives content-correct
        try:
            payload = {"result": "auth module ready", "files": ["auth.ts"], "tests": "green"}
            c.request("a2a.send", {"from": "logic-agent", "to": "planner-agent", "message": payload})
            inbox = c.request("a2a.inbox", {"agent": "planner-agent"})["messages"]
            assert len(inbox) == 1 and inbox[0]["from"] == "logic-agent"
            record("F125", "pass", ev("F125", "a2a-flow.json", inbox), "A2A-Nachricht floss Logic->Planner (im Inbox)")
            assert inbox[0]["message"] == payload  # content arrives unverändert
            record("F127", "pass", ev("F127", "a2a-content.json", inbox[0]["message"]),
                   "A2A-Nachricht-Inhalt kommt inhaltlich korrekt an")
            # drained
            assert c.request("a2a.inbox", {"agent": "planner-agent"})["messages"] == []
        except Exception as e:
            for fid in ["F125", "F127"]:
                if fid not in results: record(fid, "fail", note=str(e))

        # F122: orchestrator distributes subtasks to workers
        try:
            r = c.request("teams.decompose", {
                "subtasks": ["design tokens", "toggle state", "tests"],
                "workers": ["design-agent", "logic-agent", "test-agent"]})
            asg = {a["subtask"]: a["worker"] for a in r["assignments"]}
            assert asg["design tokens"] == "design-agent" and asg["tests"] == "test-agent"
            record("F122", "pass", ev("F122", "assign.json", r), "Subtasks konkret an Worker verteilt")
        except Exception as e:
            record("F122", "fail", note=str(e))

        # F129: team saved as reusable template
        try:
            c.request("teams.create", {"name": "darkmode-team", "orchestrator": "opus-orch",
                                       "workers": ["design-agent", "logic-agent", "test-agent"]})
            t = c.request("teams.get", {"name": "darkmode-team"})["team"]
            assert t["orchestrator"] == "opus-orch" and len(t["workers"]) == 3
            record("F129", "pass", ev("F129", "team.json", t), "Team als wiederverwendbares Template gespeichert")
        except Exception as e:
            record("F129", "fail", note=str(e))

        # F211: task workflow {{param}} substitution
        try:
            r = c.request("tasks.render", {"workflow": "Run {{tool}} against {{target}} with depth {{depth}}",
                                           "params": {"tool": "security-scan", "target": "todo-api", "depth": 3}})
            assert r["rendered"] == "Run security-scan against todo-api with depth 3"
            record("F211", "pass", ev("F211", "render.json", r), "Input-Parameter als {{param}} ersetzt")
        except Exception as e:
            record("F211", "fail", note=str(e))

        # F342: DB migration from schema diff
        try:
            r = c.request("migration.generate", {"table": "users",
                "from": {"id": "INTEGER", "name": "TEXT"},
                "to": {"id": "INTEGER", "name": "TEXT", "email": "TEXT"}})
            assert "ADD COLUMN email TEXT" in r["up"] and "DROP COLUMN email" in r["down"]
            record("F342", "pass", ev("F342", "migration.json", r), "Migration aus Schema-Diff (ADD COLUMN)")
        except Exception as e:
            record("F342", "fail", note=str(e))

        # F333: API portal from OpenAPI spec
        try:
            spec = {"paths": {"/users": {"get": {"summary": "List users"}, "post": {"summary": "Create user"}},
                              "/users/{id}": {"delete": {"summary": "Delete user"}}}}
            r = c.request("apiportal.render", {"openapi": spec})
            eps = {(e["method"], e["path"]) for e in r["endpoints"]}
            assert ("GET", "/users") in eps and ("DELETE", "/users/{id}") in eps and r["count"] == 3
            record("F333", "pass", ev("F333", "apiportal.json", r), "Endpunkt-Portal aus OpenAPI gerendert")
        except Exception as e:
            record("F333", "fail", note=str(e))

        # F323: snapshot manager detects deviations
        try:
            c.request("snapshot.save", {"name": "ui-home", "data": {"title": "Home", "items": 3}})
            same = c.request("snapshot.compare", {"name": "ui-home", "data": {"title": "Home", "items": 3}})
            diff = c.request("snapshot.compare", {"name": "ui-home", "data": {"title": "Home", "items": 5}})
            assert same["changed"] is False and diff["changed"] is True
            record("F323", "pass", ev("F323", "snapshot.json", {"same": same, "diff": diff}),
                   "Snapshot-Abweichung erkannt")
        except Exception as e:
            record("F323", "fail", note=str(e))

        # F326: accessibility WCAG check
        try:
            bad = c.request("a11y.check", {"html": '<html><body><img src="x.png"><input type="text"></body></html>'})
            good = c.request("a11y.check", {"html": '<html lang="de"><body><img src="x" alt="Logo"><label>N<input></label></body></html>'})
            rules = {v["rule"] for v in bad["violations"]}
            assert "img-alt" in rules and bad["count"] >= 1 and good["passed"] is True
            record("F326", "pass", ev("F326", "a11y.json", {"bad": bad, "good": good}),
                   "WCAG-Verstöße gelistet (img-alt etc.)")
        except Exception as e:
            record("F326", "fail", note=str(e))

        # F350: session comments pinned to a message
        try:
            c.request("comments.add", {"session_id": "s1", "message_id": "m7", "text": "this looks risky"})
            c.request("comments.add", {"session_id": "s1", "message_id": "m9", "text": "nice fix"})
            lst = c.request("comments.list", {"session_id": "s1"})["comments"]
            assert len(lst) == 2 and any(x["message_id"] == "m7" and "risky" in x["text"] for x in lst)
            record("F350", "pass", ev("F350", "comments.json", lst), "Kommentare an Nachrichten angeheftet")
        except Exception as e:
            record("F350", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
