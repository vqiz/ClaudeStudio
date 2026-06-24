#!/usr/bin/env python3
"""Verifikation LLM-Batch 2 (echter Core, ECHTER claude-Agent, echtes node --test):

  F316  Auto-Loop-Agent: ein absichtlich gebrochener Test wird vom echten claude-Agenten repariert
        — er liest die roten Tests, editiert den QUELLCODE und führt die Tests erneut aus, bis grün
        (max. 5 Iterationen). Verifiziert: am Ende grün, die Quelle wurde korrigiert, der Test blieb.
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
    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/llm2.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": CLAUDE}) as ctx:
        c = P.Client(ctx["sock"], timeout=600)
        try:
            proj = Path(tempfile.mkdtemp(prefix="cs-f316-"))
            (proj / "package.json").write_text('{"name":"f316","type":"module"}')
            # BUG: add gibt a-b zurück (falsch); der Test erwartet a+b -> rot
            (proj / "math.mjs").write_text("export function add(a, b) { return a - b; }\n")
            (proj / "math.test.mjs").write_text(
                "import { test } from 'node:test';\nimport assert from 'node:assert';\n"
                "import { add } from './math.mjs';\n"
                "test('add', () => { assert.equal(add(2, 3), 5); });\n")
            test_md5_before = (proj / "math.test.mjs").read_text()

            # Vorbedingung: Tests sind rot
            pre = subprocess.run(["node", "--test", "math.test.mjs"], cwd=proj, capture_output=True, text=True)
            assert pre.returncode != 0, "Test war nicht rot (Setup-Fehler)"

            r = c.request("code.auto_fix_loop", {"cwd": str(proj), "test_command": "node --test", "max_iter": 5})
            assert r["green"] is True, f"nicht grün nach Auto-Loop: {r}"
            assert r["iterations"] <= 11  # <= 5 (test+fix) Paare + finaler Check

            # Quelle korrigiert: add(2,3)==5 (a+b), nicht der Test verändert
            srccheck = subprocess.run(
                ["node", "-e", "import('./math.mjs').then(m=>process.exit(m.add(2,3)===5?0:1))"],
                cwd=proj, capture_output=True, text=True)
            assert srccheck.returncode == 0, "Quelle nicht korrekt repariert (add(2,3)!=5)"
            # Test blieb erhalten (claude hat nicht einfach den Test entfernt/aufgeweicht)
            test_after = (proj / "math.test.mjs").read_text()
            assert "add" in test_after and "5" in test_after, "Testdatei wurde entkernt"

            record("F316", "pass", ev("F316", "auto-fix-loop.json",
                   {"green": r["green"], "iterations": r["iterations"], "history": r["history"],
                    "test_unchanged": test_after == test_md5_before}),
                   f"Auto-Loop reparierte die Quelle in {r['iterations']} Schritten -> Tests grün")
        except Exception as e:
            record("F316", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
