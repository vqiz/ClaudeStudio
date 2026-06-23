#!/usr/bin/env python3
"""Verifikation Build-Batch 41 (echter Core, Stub-`claude`, echtes git, kein Mock):

  F068  Worktree-Status-Farben: rot=git-Fehler (detached HEAD), grün=aktiv (laufender Agent),
        gelb=arbeitend (uncommittete Änderungen), weiß=idle (sauber) — real aus git-Zustand.
  F315  OS-View Mission-Control bündelt gleichzeitig Live-Daten: laufende Agenten, Event-Stream,
        Queue-Board, A2A-Feed und Resource-Gauges.
"""
from __future__ import annotations
import json, os, subprocess, sys, time, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
STUB = ROOT / "test-harness" / "lib" / "stub_claude.sh"
results: dict[str, dict] = {}
GENV = {**os.environ, "GIT_AUTHOR_NAME": "B41", "GIT_AUTHOR_EMAIL": "b41@cs.test",
        "GIT_COMMITTER_NAME": "B41", "GIT_COMMITTER_EMAIL": "b41@cs.test"}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=GENV)


def start_longrun(c: P.Client, cwd: str) -> str:
    rid = str(uuid.uuid4())
    c.sock.sendall(P.encode_frame({"id": rid, "kind": "request", "method": "session.start",
                   "payload": {"prompt": "LONGRUN Aufgabe", "cwd": cwd, "binary": str(STUB)}}))
    sid = None
    while True:
        f = c._read_frame()
        if f.get("id") == rid and f.get("kind") != "event":
            sid = (f.get("payload") or {}).get("session_id"); continue
        if ((f.get("payload") or {}).get("event") or {}).get("kind") == "assistant_text":
            break
    return sid


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b41.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": str(STUB)}) as ctx:
        sock = ctx["sock"]
        ctrl = P.Client(sock, timeout=15)

        # F068 — vier Worktree-Status-Farben aus echtem git-Zustand
        try:
            repo = Path(__import__("tempfile").mkdtemp(prefix="cs-b41-wt-"))
            git(repo, "init", "-q", "-b", "main")
            (repo / "a.txt").write_text("1\n"); git(repo, "add", "-A"); git(repo, "commit", "-qm", "init")
            base = str(repo)
            wt_active, wt_idle, wt_dirty, wt_det = (base + s for s in ("-act", "-idle", "-dirty", "-det"))
            for path, br in ((wt_active, "act"), (wt_idle, "idle"), (wt_dirty, "dirty"), (wt_det, "det")):
                ctrl.request("worktree.add", {"cwd": base, "path": path, "branch": br})
            # aktiv: laufender Agent in wt_active
            ca = P.Client(sock, timeout=15)
            start_longrun(ca, wt_active)
            # arbeitend: uncommittete Änderung in wt_dirty
            (Path(wt_dirty) / "wip.txt").write_text("work in progress\n")
            # git-Fehler: detached HEAD in wt_det
            sha = git(repo, "rev-parse", "HEAD").stdout.strip()
            git(wt_det, "checkout", "-q", "--detach", sha)

            r = ctrl.request("worktree.status",
                             {"worktrees": [wt_active, wt_idle, wt_dirty, wt_det], "active_paths": [wt_active]})
            by = {w["path"]: w for w in r["worktrees"]}
            assert by[wt_active]["color"] == "green" and by[wt_active]["state"] == "active"
            assert by[wt_idle]["color"] == "white" and by[wt_idle]["state"] == "idle"
            assert by[wt_dirty]["color"] == "yellow" and by[wt_dirty]["dirty"]
            assert by[wt_det]["color"] == "red" and by[wt_det]["detached"]
            ca.close()
            record("F068", "pass", ev("F068", "worktree-status.json", r),
                   "grün=aktiv, weiß=idle, gelb=arbeitend, rot=detached HEAD (echter git-Zustand)")
        except Exception as e:
            record("F068", "fail", note=str(e))

        # F315 — Mission-Control bündelt alle Live-Sektionen gleichzeitig
        try:
            cb = P.Client(sock, timeout=15)
            proj = Path(__import__("tempfile").mkdtemp(prefix="cs-b41-mc-"))
            (proj / "api.ts").write_text("const q = `SELECT * FROM u WHERE id=${req.params.id}`\n")
            start_longrun(cb, str(proj))                                   # laufender Agent
            ctrl.request("rules.add", {"when": {"event": "git.push", "branch": "main"},
                                       "then": ["start_agent:security-scan"]})
            ctrl.request("events.publish", {"type": "git.push", "branch": "main", "cwd": str(proj)})  # Events + A2A
            ctrl.request("queue.enqueue", {"task": "DSGVO-Audit"})         # Queue-Board
            mc = ctrl.request("os.mission_control", {})
            assert mc["agents"]["count"] >= 1
            assert len(mc["event_stream"]) >= 1
            assert len(mc["queue_board"]) >= 1
            assert len(mc["a2a_feed"]) >= 1   # agent_started/result aus dem Scan
            g = mc["gauges"]
            assert g["running_agents"] >= 1 and g["queued_tasks"] >= 1 and g["recent_events"] >= 1
            cb.close()
            record("F315", "pass", ev("F315", "mission-control.json",
                   {"gauges": g, "agents": mc["agents"]["count"], "events": len(mc["event_stream"]),
                    "queue": len(mc["queue_board"]), "a2a": len(mc["a2a_feed"])}),
                   "Mission-Control bündelt Agenten+Event-Stream+Queue+A2A+Gauges gleichzeitig")
        except Exception as e:
            record("F315", "fail", note=str(e))

        ctrl.close()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
