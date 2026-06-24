#!/usr/bin/env python3
"""Verifikation LLM-Batch 15 (echter Core, ECHTER claude):

  F219  Aktions-Buttons auf Co-Pilot-Vorschlagskarten führen die Aktion aus: der Druck auf
        [Jetzt beheben] (action='fix_tests') startet sofort den passenden Agenten, der die roten
        Tests repariert; der Lauf erscheint im Event-Log.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
CLAUDE = os.path.expanduser("~/.local/bin/claude")
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def main():
    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/llm15.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": CLAUDE}) as ctx:
        c = P.Client(ctx["sock"], timeout=600)
        try:
            proj = Path(tempfile.mkdtemp(prefix="cs-f219-"))
            (proj / "package.json").write_text('{"name":"f219","type":"module"}')
            (proj / "math.mjs").write_text("export function add(a, b) { return a - b; }\n")  # BUG
            (proj / "math.test.mjs").write_text(
                "import { test } from 'node:test';\nimport assert from 'node:assert';\n"
                "import { add } from './math.mjs';\n"
                "test('add', () => assert.equal(add(2, 3), 5));\n")
            pre = subprocess.run(["node", "--test", "math.test.mjs"], cwd=proj, capture_output=True, text=True)
            assert pre.returncode != 0, "Test war nicht rot"

            # die Vorschlagskarte bietet die Aktion an
            sug = c.request("copilot.suggestions", {"state": {"failing_tests": 1}})["suggestions"]
            card = next(s for s in sug if s["action"] == "fix_tests")
            assert card["action_label"], "kein Aktions-Button"

            # Druck auf den Button -> Aktion ausführen
            r = c.request("copilot.run_action", {"action": "fix_tests", "cwd": str(proj),
                                                 "test_command": "node --test"})
            assert (r.get("result") or {}).get("tests_green") is True, f"Aktion reparierte nicht: {r}"
            kinds = [e.get("kind") for e in r["log"]]
            assert "copilot_action_started" in kinds and "agent_result" in kinds, f"Lauf nicht protokolliert: {kinds}"
            # Lauf ist im Event-Log/OS-Log sichtbar
            mc = c.request("os.mission_control", {})
            logged = any(e.get("action") == "fix_tests" for e in mc.get("event_stream", []))
            assert logged, "Aktion nicht im OS-Event-Log"
            record("F219", "pass", ev("F219", "copilot-action.json",
                   {"button": card["action_label"], "result": r["result"], "log": r["log"]}),
                   f"[{card['action_label']}] startete fix_tests-Agent -> Tests grün, im Event-Log sichtbar")
        except Exception as e:
            record("F219", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
