#!/usr/bin/env python3
"""Verifikation Build-Batch 31 (echter Core, Stub-`claude`, kein Mock):

  F162  'Diesen Prompt wiederholen' startet eine NEUE Session mit identischem Initial-Prompt
        (neue session_id, erste User-Nachricht == Original-Prompt).
  F307  Event 'test.failed' startet automatisch einen Fix-Agenten, der den fehlgeschlagenen
        Test real aufnimmt (führt das Test-Kommando aus, erfasst die rote Ausgabe). Ein nicht
        passendes Event feuert die Regel nicht.
"""
from __future__ import annotations
import json, subprocess, sys, tempfile, time, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
STUB = ROOT / "test-harness" / "lib" / "stub_claude.sh"
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def run_to_done(c: P.Client, prompt: str, cwd: str) -> str:
    """session.start + Stream bis 'result' lesen; liefert die session_id."""
    rid = str(uuid.uuid4())
    env = {"id": rid, "kind": "request", "method": "session.start",
           "payload": {"prompt": prompt, "cwd": cwd, "binary": str(STUB)}}
    c.sock.sendall(P.encode_frame(env))
    sid = None
    deadline = time.time() + 15
    while time.time() < deadline:
        f = c._read_frame()
        if f.get("id") == rid and f.get("kind") != "event":
            sid = (f.get("payload") or {}).get("session_id")
            continue
        if f.get("method") == "session.event" or f.get("kind") == "event":
            evd = (f.get("payload") or {}).get("event") or {}
            if evd.get("kind") in ("result", "stopped", "error"):
                break
    return sid


def first_user_text(messages) -> str:
    for m in messages:
        if m.get("role") == "user":
            return m.get("content") or m.get("text") or ""
    return ""


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b31.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": str(STUB)}) as ctx:
        sock = ctx["sock"]

        # F162 — Prompt wiederholen erzeugt neue Session mit identischem Initial-Prompt
        try:
            prompt = "Implementiere ein Health-Endpoint für todo-api"
            c = P.Client(sock, timeout=15)
            sid1 = run_to_done(c, prompt, str(ROOT))
            assert sid1
            msgs1 = c.request("session.messages", {"id": sid1})["messages"]
            assert first_user_text(msgs1) == prompt, first_user_text(msgs1)
            # 'Diesen Prompt wiederholen' -> neue Session, derselbe Initial-Prompt
            sid2 = run_to_done(c, prompt, str(ROOT))
            assert sid2 and sid2 != sid1
            msgs2 = c.request("session.messages", {"id": sid2})["messages"]
            assert first_user_text(msgs2) == prompt
            c.close()
            record("F162", "pass", ev("F162", "repeat-prompt.json",
                   {"original_session": sid1, "repeated_session": sid2,
                    "initial_prompt": prompt, "identical": True}),
                   "Wiederholung erzeugte neue Session mit identischem Initial-Prompt")
        except Exception as e:
            record("F162", "fail", note=str(e))

        # F307 — test.failed startet Fix-Agent, der den roten Test real aufnimmt
        try:
            c = P.Client(sock, timeout=20)
            c.request("rules.add", {"when": {"event": "test.failed"}, "then": ["start_agent:fix-tests"]})
            proj = Path(tempfile.mkdtemp(prefix="cs-b31-fix-"))
            res = c.request("events.publish", {
                "type": "test.failed", "cwd": str(proj),
                "test": "test_health_returns_200",
                "test_command": "python3 -c 'import sys; sys.exit(1)'"})  # echter roter Test
            log = res["log"]
            kinds = [e["kind"] for e in log]
            started = next(e for e in log if e["kind"] == "agent_started" and e["agent"] == "fix-agent")
            result = next(e for e in log if e["kind"] == "agent_result" and e["agent"] == "fix-agent")
            assert res["fired"] >= 1
            assert started["picked_up_test"] == "test_health_returns_200"
            assert result["test_red"] is True and result["test_exit"] == 1
            cids = {e["correlation_id"] for e in log}
            assert len(cids) == 1  # eine Korrelations-ID über den ganzen Flow
            # nicht passendes Event feuert die Regel nicht
            nofire = c.request("events.publish", {"type": "test.passed", "cwd": str(proj)})
            assert nofire["fired"] == 0
            c.close()
            record("F307", "pass", ev("F307", "fix-agent.json",
                   {"on_test_failed": res, "on_test_passed_fired": nofire["fired"]}),
                   "test.failed -> fix-agent gestartet, roter Test (exit 1) aufgenommen; test.passed feuert nicht")
        except Exception as e:
            record("F307", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
