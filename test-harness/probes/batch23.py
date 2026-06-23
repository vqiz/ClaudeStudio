#!/usr/bin/env python3
"""Verifikation Build-Batch 23: Team-Edges (F121), Review-Gate vor Merge (F124),
A2A-Eskalation (F128), WorktreeRemove-Hook committet (F264). Echter Core + git. Kein Mock.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}
GENV = {**os.environ, "GIT_AUTHOR_NAME": "B23", "GIT_AUTHOR_EMAIL": "b23@cs.test",
        "GIT_COMMITTER_NAME": "B23", "GIT_COMMITTER_EMAIL": "b23@cs.test"}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=GENV)


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b23.log")) as ctx:
        c = P.Client(ctx["sock"])

        # F121 team builder edges Orchestrator->Worker1/Worker2
        try:
            c.request("teams.create", {"name": "auth-team", "orchestrator": "opus-orch", "workers": ["design-w", "logic-w"]})
            t = c.request("teams.get", {"name": "auth-team"})["team"]
            edges = {tuple(e) for e in t["edges"]}
            assert ("opus-orch", "design-w") in edges and ("opus-orch", "logic-w") in edges
            record("F121", "pass", ev("F121", "team-edges.json", t), "persistierte Team-Edges Orchestrator->Worker exakt")
        except Exception as e:
            record("F121", "fail", note=str(e))

        # F124 review-gate: approved logged BEFORE merge; merge is real
        try:
            repo = Path(tempfile.mkdtemp(prefix="cs-b23-rev-"))
            git(repo, "init", "-q", "-b", "main"); (repo / "base").write_text("0\n"); git(repo, "add", "-A"); git(repo, "commit", "-qm", "init")
            git(repo, "checkout", "-q", "-b", "feature"); (repo / "feat.txt").write_text("worker work\n"); git(repo, "add", "-A"); git(repo, "commit", "-qm", "feat")
            git(repo, "checkout", "-q", "main")
            r = c.request("teams.review_and_merge", {"cwd": str(repo), "branch": "feature", "worker_ok": True})
            kinds = [e["kind"] for e in r["log"]]
            assert kinds.index("review_approved") < kinds.index("merged")
            assert r["merged"] is True and (repo / "feat.txt").exists()
            # rejected path does not merge
            rej = c.request("teams.review_and_merge", {"cwd": str(repo), "branch": "feature", "worker_ok": False})
            assert rej["merged"] is False and rej["decision"] == "rejected"
            record("F124", "pass", ev("F124", "review-gate.json", {"approved": r, "rejected": rej}),
                   "Merge erst nach 'approved'-Review (Log-Reihenfolge), echter git merge")
        except Exception as e:
            record("F124", "fail", note=str(e))

        # F128 failed worker escalates to orchestrator -> reassign/fail decision
        try:
            r1 = c.request("teams.escalate", {"worker": "logic-w", "orchestrator": "opus-orch",
                                              "subtask": "edit /nonexistent.ts", "error": "ENOENT", "attempts": 1})
            assert r1["escalated"] is True and r1["decision"] == "reassign"
            msgs = r1["orchestrator_inbox"]
            assert any(m["from"] == "logic-w" and m["message"]["status"] == "failed" for m in msgs)
            r2 = c.request("teams.escalate", {"worker": "logic-w", "orchestrator": "opus-orch2",
                                              "subtask": "x", "error": "boom", "attempts": 3})
            assert r2["decision"] == "fail"
            record("F128", "pass", ev("F128", "escalate.json", {"reassign": r1, "fail": r2}),
                   "Worker->Orchestrator-Eskalation floss; Reassign/Fail-Entscheidung")
        except Exception as e:
            record("F128", "fail", note=str(e))

        # F264 WorktreeRemove hook commits the worktree changes
        try:
            repo = Path(tempfile.mkdtemp(prefix="cs-b23-wt-"))
            git(repo, "init", "-q", "-b", "main"); (repo / "a").write_text("x"); git(repo, "add", "-A"); git(repo, "commit", "-qm", "init")
            wt = str(repo) + "-feat"
            c.request("worktree.add", {"cwd": str(repo), "path": wt, "branch": "feat"})
            (Path(wt) / "new.txt").write_text("worktree change\n")
            # configure the WorktreeRemove hook (commit on finish) and fire it in the worktree
            c.request("hooks.add", {"cwd": wt, "event": "WorktreeRemove",
                                    "command": 'git add -A && git -c user.name=H -c user.email=h@h commit -qm "worktree done"'})
            run = c.request("hooks.run", {"cwd": wt, "event": "WorktreeRemove"})
            log = subprocess.run(["git", "-C", wt, "log", "--oneline"], capture_output=True, text=True).stdout
            assert "worktree done" in log and run["fired"]
            record("F264", "pass", ev("F264", "worktree-commit.json", {"git_log": log, "run": run}),
                   "WorktreeRemove-Hook committet die Worktree-Änderungen (git log)")
        except Exception as e:
            record("F264", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
