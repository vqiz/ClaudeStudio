#!/usr/bin/env python3
"""Verifikation Build-Batch 35 (echter Core, Stub-`claude`, echtes git-worktree, kein Mock):

  F110  Isolation-Toggle Worktree: bei isolation='worktree' legt der Core einen frischen
        git-Worktree (eigener Branch) an und lässt den Agenten dort laufen; der Datei-Edit
        landet im Worktree, NICHT im Hauptcheckout. 'git worktree list'/'git branch' zeigen ihn.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile, time, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
STUB = ROOT / "test-harness" / "lib" / "stub_claude.sh"
results: dict[str, dict] = {}
GENV = {**os.environ, "GIT_AUTHOR_NAME": "B35", "GIT_AUTHOR_EMAIL": "b35@cs.test",
        "GIT_COMMITTER_NAME": "B35", "GIT_COMMITTER_EMAIL": "b35@cs.test"}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=GENV)


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b35.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": str(STUB)}) as ctx:
        c = P.Client(ctx["sock"], timeout=15)
        try:
            repo = Path(tempfile.mkdtemp(prefix="cs-b35-iso-"))
            git(repo, "init", "-q", "-b", "main")
            (repo / "app.py").write_text("x = 1\n")
            git(repo, "add", "-A"); git(repo, "commit", "-qm", "init")

            rid = str(uuid.uuid4())
            c.sock.sendall(P.encode_frame({"id": rid, "kind": "request", "method": "session.start",
                "payload": {"prompt": "Bitte EDITFILE im isolierten Worktree", "cwd": str(repo),
                            "binary": str(STUB), "isolation": "worktree"}}))
            worktree = None
            deadline = time.time() + 15
            while time.time() < deadline:
                f = c._read_frame()
                if f.get("id") == rid and f.get("kind") != "event":
                    worktree = (f.get("payload") or {}).get("worktree"); continue
                if f.get("method") == "session.event" or f.get("kind") == "event":
                    if ((f.get("payload") or {}).get("event") or {}).get("kind") == "done":
                        break

            assert worktree and worktree.get("path"), f"kein Worktree im Ack: {worktree}"
            wt = Path(worktree["path"])
            branch = worktree["branch"]
            # git kennt den Worktree + den Branch
            wt_list = git(repo, "worktree", "list").stdout
            branches = git(repo, "branch", "--all").stdout
            assert str(wt) in wt_list, wt_list
            assert branch.replace("agent/", "") in branches or branch in branches, branches
            # der Agent-Edit liegt IM Worktree, NICHT im Hauptcheckout (Isolation)
            assert (wt / "agent_edit.txt").exists(), "Edit fehlt im Worktree"
            assert not (repo / "agent_edit.txt").exists(), "Edit ist in den Hauptcheckout geleckt"
            record("F110", "pass", ev("F110", "worktree-isolation.json",
                   {"worktree": worktree, "worktree_list": wt_list.strip(),
                    "edit_in_worktree": True, "edit_in_main": False}),
                   f"Agent lief isoliert im Worktree {wt.name} (Branch {branch}); Edit blieb isoliert")
        except Exception as e:
            record("F110", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
