#!/usr/bin/env python3
"""Verifikation Build-Batch 7: Secret-Scanner, Hook-Builder, Deploy-Risiko.

Neu (router.rs): git.secret_scan (scannt echte git-History), hooks.add/remove
(echtes .claude/settings.json im Standard-Claude-Code-Format), deploy.risk
(Diff-Analyse Low/Medium/High). Plus F267 = git.commit_message (Deployment-
Commit-Assistent). Echte git-Repos, echter Core. Kein Mock.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}
GENV = {**os.environ, "GIT_AUTHOR_NAME": "B7", "GIT_AUTHOR_EMAIL": "b7@cs.test",
        "GIT_COMMITTER_NAME": "B7", "GIT_COMMITTER_EMAIL": "b7@cs.test"}


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


def fresh_repo(name):
    repo = Path(tempfile.mkdtemp(prefix=f"cs-b7-{name}-"))
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("# repo\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "init")
    return repo


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b7.log")) as ctx:
        c = P.Client(ctx["sock"])

        # F075: secret scanner finds a key committed to git history
        try:
            repo = fresh_repo("secret")
            (repo / "config.txt").write_text("aws_access_key_id=AKIAIOSFODNN7EXAMPLE\napi_key = sk_live_0123456789abcdefXYZ\n")
            git(repo, "add", "-A"); git(repo, "commit", "-qm", "add config")
            # overwrite + commit again so the secret is only in history, not the worktree
            (repo / "config.txt").write_text("AWS_SECRET_ACCESS_KEY=<redacted>\n")
            git(repo, "add", "-A"); git(repo, "commit", "-qm", "scrub")
            r = c.request("git.secret_scan", {"cwd": str(repo)})
            kinds = {f["kind"] for f in r["findings"]}
            assert r["found"] and ("aws_access_key" in kinds)
            record("F075", "pass", ev("F075", "secret-scan.json", r),
                   f"Secret in History gefunden: {sorted(kinds)}")
        except Exception as e:
            record("F075", "fail", note=str(e))

        # F257: hook builder writes a real hook; hooks.list returns it; remove works
        try:
            repo = fresh_repo("hooks")
            c.request("hooks.add", {"cwd": str(repo), "event": "PostToolUse",
                                    "matcher": "Edit|Write", "command": "prettier --write $FILE"})
            listed = c.request("hooks.list", {"cwd": str(repo)})["hooks"]
            mine = [h for h in listed if h.get("event") == "PostToolUse"]
            settings = json.loads((repo / ".claude/settings.json").read_text())
            assert mine and settings["hooks"]["PostToolUse"][0]["matcher"] == "Edit|Write"
            rm = c.request("hooks.remove", {"cwd": str(repo), "event": "PostToolUse", "matcher": "Edit|Write"})
            after = c.request("hooks.list", {"cwd": str(repo)})["hooks"]
            assert rm["removed"] == 1 and not [h for h in after if h.get("event") == "PostToolUse"]
            record("F257", "pass", ev("F257", "hook-builder.json", {"added": mine, "settings": settings, "removed": rm}),
                   "Hook (Ereignis+Matcher+Command) in .claude/settings.json gebaut + entfernt")
        except Exception as e:
            record("F257", "fail", note=str(e))

        # F267: deployment commit-assistant generates a conventional message from the diff
        try:
            repo = fresh_repo("commit")
            (repo / "feature.ts").write_text("export const f = () => 1\n")
            git(repo, "add", "-A")
            msg = c.request("git.commit_message", {"cwd": str(repo)})["message"]
            assert ":" in msg and len(msg) > 5  # conventional "type: subject"
            record("F267", "pass", ev("F267", "commit-msg.json", {"message": msg}),
                   f"Conventional-Commit-Nachricht generiert: '{msg}'")
        except Exception as e:
            record("F267", "fail", note=str(e))

        # F275: deploy risk predictor — migration => high, tiny change => low
        try:
            repo = fresh_repo("risk")
            (repo / "migrations").mkdir()
            (repo / "migrations" / "001_add_table.sql").write_text("CREATE TABLE x(id int);\n")
            git(repo, "add", "-A")
            high = c.request("deploy.risk", {"cwd": str(repo)})
            git(repo, "commit", "-qm", "migration")
            (repo / "README.md").write_text("# repo\nminor tweak\n")
            git(repo, "add", "-A")
            low = c.request("deploy.risk", {"cwd": str(repo)})
            assert high["risk"] == "high" and any("Migration" in r for r in high["reasons"])
            assert low["risk"] == "low"
            record("F275", "pass", ev("F275", "deploy-risk.json", {"migration": high, "tiny": low}),
                   "Deploy-Risiko: Migration->high, Mini-Änderung->low")
        except Exception as e:
            record("F275", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
