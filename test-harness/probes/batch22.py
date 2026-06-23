#!/usr/bin/env python3
"""Verifikation Build-Batch 22: Event-Bus mit echter Scan-Dispatch (F306), Abhaengigkeits-DAG
(F311), Queue-Reorder (F312). Echter Core. Kein Mock.
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
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b22.log")) as ctx:
        c = P.Client(ctx["sock"])

        # F306 event git.push on main -> security-scan agent runs a REAL scan
        try:
            c.request("rules.add", {"when": {"event": "git.push", "branch": "main"},
                                    "then": ["start_agent:security-scan"]})
            proj = Path(tempfile.mkdtemp(prefix="cs-b22-proj-"))
            (proj / "api.ts").write_text("const q = `SELECT * FROM u WHERE id=${req.params.id}`\n")
            res = c.request("events.publish", {"type": "git.push", "branch": "main", "cwd": str(proj)})
            log = res["log"]
            kinds = [e["kind"] for e in log]
            cids = {e["correlation_id"] for e in log}
            assert kinds[0] == "event" and "agent_started" in kinds and "agent_result" in kinds
            assert len(cids) == 1  # same correlation id throughout
            assert res["scan"]["count"] >= 1  # the real scan found the SQL-injection
            # a non-main branch must NOT fire the rule
            nofire = c.request("events.publish", {"type": "git.push", "branch": "feature/x", "cwd": str(proj)})
            assert nofire["fired"] == 0
            record("F306", "pass", ev("F306", "event-scan.json", {"on_main": res, "on_feature_fired": nofire["fired"]}),
                   "git.push@main -> security-scan-agent started + echtes Scan-Ergebnis (1 Korrelations-ID)")
        except Exception as e:
            record("F306", "fail", note=str(e))

        # F311 dependency DAG: A -> B -> C, B/C blocked until A done
        try:
            tasks = [{"id": "A"}, {"id": "B", "deps": ["A"]}, {"id": "C", "deps": ["B"]}]
            dag = c.request("queue.dag", {"tasks": tasks})
            edges = {tuple(e) for e in dag["edges"]}
            assert ("A", "B") in edges and ("B", "C") in edges
            assert dag["order"] == ["A", "B", "C"]
            assert dag["blocked"]["B"] == ["A"] and dag["blocked"]["C"] == ["B"]
            # once A is done, B becomes ready
            dag2 = c.request("queue.dag", {"tasks": tasks, "done": ["A"]})
            assert "B" in dag2["ready"] and "C" in dag2["blocked"]
            record("F311", "pass", ev("F311", "dag.json", {"dag": dag, "after_A_done": dag2}),
                   "DAG A->B->C, Vorgaenger-Blockierung korrekt (nach A: B ready)")
        except Exception as e:
            record("F311", "fail", note=str(e))

        # F312 manual reprioritize: drag a back task to the front -> runs next
        try:
            r = c.request("queue.reorder", {"queue": ["t1", "t2", "t3", "t4"], "move": "t4", "to": 0})
            assert r["order"] == ["t4", "t1", "t2", "t3"] and r["next"] == "t4"
            record("F312", "pass", ev("F312", "reorder.json", r),
                   "per Reorder nach vorne gezogener Task läuft als nächster")
        except Exception as e:
            record("F312", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
