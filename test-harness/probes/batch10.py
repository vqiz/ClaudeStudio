#!/usr/bin/env python3
"""Verifikation Build-Batch 10: Deployment/DevOps/Produktivität gegen den echten Core.

Neu (router.rs): env.add/list, deploy.rollback (echtes git reset), deploy.checklist,
flags.set/eval/list, metrics.dora, report.standup, checkpoint.save/restore/list.
Plus F274=git.secret_scan, F327=deploy.risk (Wiederverwendung). Echte git-Repos.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}
GENV = {**os.environ, "GIT_AUTHOR_NAME": "B10", "GIT_AUTHOR_EMAIL": "b10@cs.test",
        "GIT_COMMITTER_NAME": "B10", "GIT_COMMITTER_EMAIL": "b10@cs.test"}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def git(repo, *a, check=True):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=GENV)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(a)}: {r.stderr.strip()}")
    return r.stdout.strip()


def repo_with(name, commits):
    repo = Path(tempfile.mkdtemp(prefix=f"cs-b10-{name}-"))
    git(repo, "init", "-q", "-b", "main")
    for i, (fn, body, msg) in enumerate(commits):
        (repo / fn).write_text(body)
        git(repo, "add", "-A"); git(repo, "commit", "-qm", msg)
    return repo


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b10.log")) as ctx:
        c = P.Client(ctx["sock"])

        # F271 environment manager
        try:
            for n, st in [("local", "running"), ("staging", "running"), ("production", "healthy")]:
                c.request("env.add", {"name": n, "status": st, "config": {"url": f"https://{n}.example"}})
            envs = c.request("env.list", {})["environments"]
            assert set(envs) == {"local", "staging", "production"} and envs["production"]["status"] == "healthy"
            record("F271", "pass", ev("F271", "env.json", envs), "local/staging/production mit Status+Config")
        except Exception as e:
            record("F271", "fail", note=str(e))

        # F273 rollback
        try:
            repo = repo_with("rb", [("a.txt", "v1\n", "release v1"), ("a.txt", "v2-broken\n", "release v2")])
            first = git(repo, "rev-parse", "HEAD~1")
            r = c.request("deploy.rollback", {"cwd": str(repo)})
            assert r["to"] == first and (repo / "a.txt").read_text() == "v1\n"
            record("F273", "pass", ev("F273", "rollback.json", r), "Deployment auf vorherigen Commit zurückgesetzt")
        except Exception as e:
            record("F273", "fail", note=str(e))

        # F274 secret scanner (deployment category)
        try:
            repo = repo_with("sec", [("c.txt", "aws_access_key_id=AKIAIOSFODNN7EXAMPLE\n", "add key")])
            r = c.request("git.secret_scan", {"cwd": str(repo)})
            assert r["found"] and any(f["kind"] == "aws_access_key" for f in r["findings"])
            record("F274", "pass", ev("F274", "secret.json", r), "committeter API-Key in History gefunden")
        except Exception as e:
            record("F274", "fail", note=str(e))

        # F276 pre-deploy checklist blocks while a check fails
        try:
            red = c.request("deploy.checklist", {"checks": [
                {"name": "tests", "pass": True}, {"name": "cve", "pass": False}, {"name": "envvars", "pass": True}]})
            green = c.request("deploy.checklist", {"checks": [
                {"name": "tests", "pass": True}, {"name": "cve", "pass": True}]})
            assert red["blocked"] is True and red["status"] == "red" and "cve" in [str(x) for x in red["failing"]]
            assert green["blocked"] is False and green["status"] == "green"
            record("F276", "pass", ev("F276", "checklist.json", {"red": red, "green": green}),
                   "Checklist blockt Deploy bei rotem Punkt")
        except Exception as e:
            record("F276", "fail", note=str(e))

        # F327 deploy risk predictor (devops)
        try:
            repo = repo_with("risk", [("README.md", "# r\n", "init")])
            (repo / "migrations").mkdir(); (repo / "migrations" / "001.sql").write_text("CREATE TABLE t(id int);\n")
            git(repo, "add", "-A")
            r = c.request("deploy.risk", {"cwd": str(repo)})
            assert r["risk"] == "high"
            record("F327", "pass", ev("F327", "risk.json", r), "Deploy-Risiko aus Diff bewertet (Migration->high)")
        except Exception as e:
            record("F327", "fail", note=str(e))

        # F330 feature flags
        try:
            c.request("flags.set", {"name": "dark_mode", "enabled": True})
            on = c.request("flags.eval", {"name": "dark_mode"})["enabled"]
            c.request("flags.set", {"name": "dark_mode", "enabled": False})
            off = c.request("flags.eval", {"name": "dark_mode"})["enabled"]
            assert on is True and off is False
            record("F330", "pass", ev("F330", "flags.json", {"on": on, "off": off}),
                   "Feature-Flag angelegt/geschaltet, Eval reagiert")
        except Exception as e:
            record("F330", "fail", note=str(e))

        # F331 DORA metrics
        try:
            repo = repo_with("dora", [("a", "1", "feat: a"), ("b", "2", "fix: bug b"), ("c", "3", "feat: c")])
            m = c.request("metrics.dora", {"cwd": str(repo)})
            assert m["total_commits"] == 3 and abs(m["change_failure_rate"] - (1 / 3)) < 1e-6
            assert "deployment_frequency_per_day" in m and "lead_time_hours" in m
            record("F331", "pass", ev("F331", "dora.json", m),
                   "DORA: Freq/Lead-Time/Change-Failure-Rate aus git berechnet")
        except Exception as e:
            record("F331", "fail", note=str(e))

        # F352 standup report
        try:
            repo = repo_with("standup", [("x", "1", "feat: add x"), ("y", "2", "fix: y")])
            r = c.request("report.standup", {"cwd": str(repo), "since": "1 year ago"})
            assert r["commit_count"] == 2 and "add x" in r["report"]
            record("F352", "pass", ev("F352", "standup.json", r), "Standup-Report aus Git-Aktivität + Sessions")
        except Exception as e:
            record("F352", "fail", note=str(e))

        # F353 context checkpoint save/restore
        try:
            c.request("checkpoint.save", {"name": "before-refactor", "data": {"open_files": ["a.ts"], "note": "WIP"}})
            r = c.request("checkpoint.restore", {"name": "before-refactor"})["checkpoint"]
            lst = c.request("checkpoint.list", {})["checkpoints"]
            assert r["data"]["note"] == "WIP" and "before-refactor" in lst
            record("F353", "pass", ev("F353", "checkpoint.json", {"restored": r, "list": lst}),
                   "Context-Checkpoint benannt gespeichert + wiederhergestellt")
        except Exception as e:
            record("F353", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
