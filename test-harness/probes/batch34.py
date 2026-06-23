#!/usr/bin/env python3
"""Verifikation Build-Batch 34 (echter Core, Stub-`claude`, echtes git-worktree, kein Mock):

  F123  Parallelisierung: zwei Team-Worker laufen gleichzeitig, jeder in einem eigenen
        git-Worktree (os.running_agents == 2, git worktree list zeigt beide Worktrees).
  F130  Team-Lauf zentral stoppen: ein teams.stop-Aufruf beendet alle laufenden Worker
        (os.running_agents fällt auf 0).
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile, time, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
STUB = ROOT / "test-harness" / "lib" / "stub_claude.sh"
results: dict[str, dict] = {}
GENV = {**os.environ, "GIT_AUTHOR_NAME": "B34", "GIT_AUTHOR_EMAIL": "b34@cs.test",
        "GIT_COMMITTER_NAME": "B34", "GIT_COMMITTER_EMAIL": "b34@cs.test"}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=GENV)


def start(c: P.Client, prompt: str, cwd: str) -> str:
    rid = str(uuid.uuid4())
    c.sock.sendall(P.encode_frame({"id": rid, "kind": "request", "method": "session.start",
                   "payload": {"prompt": prompt, "cwd": cwd, "binary": str(STUB)}}))
    while True:
        f = c._read_frame()
        if f.get("id") == rid and f.get("kind") != "event":
            return (f.get("payload") or {}).get("session_id")


def drain_first(c: P.Client, max_wait=6):
    deadline = time.time() + max_wait
    while time.time() < deadline:
        f = c._read_frame()
        if f.get("method") == "session.event" or f.get("kind") == "event":
            evd = (f.get("payload") or {}).get("event") or {}
            if evd.get("kind") == "assistant_text":
                return


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b34.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": str(STUB)}) as ctx:
        sock = ctx["sock"]
        ctrl = P.Client(sock, timeout=15)
        try:
            # Repo + Team + zwei Worktrees (je ein Worker-Worktree)
            repo = Path(tempfile.mkdtemp(prefix="cs-b34-team-"))
            git(repo, "init", "-q", "-b", "main")
            (repo / "app.py").write_text("x = 1\n")
            git(repo, "add", "-A"); git(repo, "commit", "-qm", "init")
            ctrl.request("teams.create", {"name": "auth-team", "orchestrator": "opus-orch",
                                          "workers": ["worker-a", "worker-b"]})
            wt_a, wt_b = str(repo) + "-a", str(repo) + "-b"
            ctrl.request("worktree.add", {"cwd": str(repo), "path": wt_a, "branch": "feat-a"})
            ctrl.request("worktree.add", {"cwd": str(repo), "path": wt_b, "branch": "feat-b"})

            # je Worker eine parallele LONGRUN-Session im eigenen Worktree
            ca, cb = P.Client(sock, timeout=15), P.Client(sock, timeout=15)
            sid_a = start(ca, "LONGRUN worker-a Aufgabe", wt_a); drain_first(ca)
            sid_b = start(cb, "LONGRUN worker-b Aufgabe", wt_b); drain_first(cb)

            # F123 — beide Worker laufen parallel, jeder in eigenem Worktree
            try:
                ra = ctrl.request("os.running_agents", {})
                wts = ctrl.request("git.worktrees", {"cwd": str(repo)})["worktrees"]
                wt_paths = " ".join(json.dumps(wts))
                assert ra["count"] == 2 and set(ra["running"]) == {sid_a, sid_b}
                assert any("-a" in json.dumps(w) for w in wts) and any("-b" in json.dumps(w) for w in wts)
                record("F123", "pass", ev("F123", "parallel-workers.json",
                       {"running": ra, "worktrees": wts}),
                       "zwei Worker laufen parallel (running=2), je in eigenem git-Worktree (feat-a/feat-b)")
            except Exception as e:
                record("F123", "fail", note=str(e))

            # F130 — zentraler Team-Stop beendet alle Worker
            try:
                stop = ctrl.request("teams.stop", {"sessions": [sid_a, sid_b]})
                assert stop["count"] == 2 and set(stop["stopped"]) == {sid_a, sid_b}
                after = None
                for _ in range(40):
                    time.sleep(0.1)
                    after = ctrl.request("os.running_agents", {})
                    if after["count"] == 0:
                        break
                assert after["count"] == 0, after
                record("F130", "pass", ev("F130", "team-stop.json",
                       {"stop_result": stop, "after": after}),
                       "ein teams.stop beendete beide Worker (running 2 -> 0)")
            except Exception as e:
                record("F130", "fail", note=str(e))
            ca.close(); cb.close()
        finally:
            ctrl.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
