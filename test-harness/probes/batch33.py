#!/usr/bin/env python3
"""Verifikation Build-Batch 33 (echter Core, Stub-`claude`, echtes git, kein Mock):

  F116  Post-Run-Hook 'git commit' feuert nachweislich nach Lauf-Ende: der Agent ändert eine
        Datei, der konfigurierte Hook committet sie automatisch — 'git log -1' zeigt den neuen
        Commit, und der Core meldet ein post_run_hook-Event mit Exit 0.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile, time, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
STUB = ROOT / "test-harness" / "lib" / "stub_claude.sh"
results: dict[str, dict] = {}
GENV = {**os.environ, "GIT_AUTHOR_NAME": "B33", "GIT_AUTHOR_EMAIL": "b33@cs.test",
        "GIT_COMMITTER_NAME": "B33", "GIT_COMMITTER_EMAIL": "b33@cs.test"}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=GENV)


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b33.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": str(STUB)}) as ctx:
        c = P.Client(ctx["sock"], timeout=15)
        try:
            repo = Path(tempfile.mkdtemp(prefix="cs-b33-postrun-"))
            git(repo, "init", "-q", "-b", "main")
            (repo / "README.md").write_text("init\n")
            git(repo, "add", "-A"); git(repo, "commit", "-qm", "init")
            commits_before = git(repo, "rev-list", "--count", "HEAD").stdout.strip()

            # Agent-Lauf mit Datei-Edit (EDITFILE) + Post-Run-Hook 'git commit'
            hook = ('git add -A && git -c user.name=Hook -c user.email=h@h '
                    'commit -m "agent run committed by post-run hook"')
            rid = str(uuid.uuid4())
            c.sock.sendall(P.encode_frame({"id": rid, "kind": "request", "method": "session.start",
                "payload": {"prompt": "Bitte EDITFILE bearbeiten", "cwd": str(repo),
                            "binary": str(STUB), "post_run_hook": hook}}))
            sid = None
            hook_event = None
            deadline = time.time() + 15
            while time.time() < deadline:
                f = c._read_frame()
                if f.get("id") == rid and f.get("kind") != "event":
                    sid = (f.get("payload") or {}).get("session_id"); continue
                if f.get("method") == "session.event" or f.get("kind") == "event":
                    evd = (f.get("payload") or {}).get("event") or {}
                    if evd.get("kind") == "post_run_hook":
                        hook_event = evd
                    if evd.get("kind") == "done":
                        break
            assert sid, "keine session_id"
            assert hook_event is not None, "kein post_run_hook-Event"
            assert hook_event["exit"] == 0, hook_event

            # echte Verifikation am Repo: ein neuer Commit, die Agent-Datei ist drin
            commits_after = git(repo, "rev-list", "--count", "HEAD").stdout.strip()
            last_msg = git(repo, "log", "-1", "--pretty=%s").stdout.strip()
            tracked = git(repo, "ls-files").stdout
            assert int(commits_after) == int(commits_before) + 1, f"{commits_before}->{commits_after}"
            assert "post-run hook" in last_msg
            assert "agent_edit.txt" in tracked
            record("F116", "pass", ev("F116", "post-run-commit.json",
                   {"session_id": sid, "commits_before": commits_before, "commits_after": commits_after,
                    "last_commit": last_msg, "hook_event": hook_event}),
                   f"Post-Run-Hook committete die Agent-Änderung (git log -1: '{last_msg}')")
        except Exception as e:
            record("F116", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
