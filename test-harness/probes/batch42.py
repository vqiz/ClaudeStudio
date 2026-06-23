#!/usr/bin/env python3
"""Verifikation Build-Batch 42 (echter Core, echtes node --test --experimental-test-coverage):

  F204  Test-Coverage-Report-Task ermittelt die ECHTE Test-Abdeckung (ausgeführte Coverage,
        keine Heuristik): untested Funktionen senken die gemessene Coverage.
  F322  Coverage-Dashboard zeigt Gesamt-Coverage-% und mindestens drei Module mit je %-Wert.

Coverage wird mit Node 22s eingebautem Test-Runner (lcov-Reporter) erzeugt — kein npm-Install nötig.
"""
from __future__ import annotations
import json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b42.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=60)
        try:
            proj = Path(tempfile.mkdtemp(prefix="cs-b42-cov-"))
            (proj / "math.js").write_text(
                "function add(a,b){return a+b;}\nfunction sub(a,b){return a-b;}\n"
                "function mul(a,b){return a*b;}\nmodule.exports={add,sub,mul};\n")
            (proj / "util.js").write_text(
                "function greet(n){return 'hi '+n;}\nfunction bye(n){return 'bye '+n;}\n"
                "module.exports={greet,bye};\n")
            (proj / "test.mjs").write_text(
                "import { test } from 'node:test';\nimport assert from 'node:assert';\n"
                "import { add, sub } from './math.js';\nimport { greet } from './util.js';\n"
                "test('add', () => assert.equal(add(1,2),3));\n"
                "test('sub', () => assert.equal(sub(5,2),3));\n"
                "test('greet', () => assert.equal(greet('x'),'hi x'));\n")
            cmd = ("node --test --experimental-test-coverage --test-reporter=lcov "
                   "--test-reporter-destination=coverage.lcov ./test.mjs")
            r = c.request("coverage.measure", {"cwd": str(proj), "command": cmd, "lcov": "coverage.lcov"})

            mods = {Path(m["file"]).name: m for m in r["modules"]}
            total = r["total_percent"]

            # F322 — Dashboard: Gesamt-% + >= 3 Module mit je %-Wert
            try:
                assert r["module_count"] >= 3, r["module_count"]
                assert isinstance(total, (int, float)) and 0 < total <= 100
                assert all("percent" in m for m in r["modules"])
                record("F322", "pass", ev("F322", "coverage-dashboard.json",
                       {"total_percent": total, "module_count": r["module_count"],
                        "modules": [{"file": Path(m["file"]).name, "percent": m["percent"]} for m in r["modules"]]}),
                       f"Gesamt-Coverage {total}% über {r['module_count']} Module (je mit %-Wert)")
            except Exception as e:
                record("F322", "fail", note=str(e))

            # F204 — echte Coverage: ungetestete Funktionen (mul/bye) senken die Abdeckung
            try:
                assert r["lines_found"] > 0 and r["lines_hit"] < r["lines_found"]  # partiell, echt gemessen
                assert total < 100  # nicht alles abgedeckt
                assert mods["math.js"]["percent"] < 100  # mul ungetestet
                record("F204", "pass", ev("F204", "coverage-report.json",
                       {"total_percent": total, "lines_hit": r["lines_hit"], "lines_found": r["lines_found"],
                        "math_js_percent": mods["math.js"]["percent"]}),
                       f"echte Coverage {total}% (math.js {mods['math.js']['percent']}%, mul/bye ungetestet)")
            except Exception as e:
                record("F204", "fail", note=str(e))
        except Exception as e:
            record("F204", "fail", note=str(e)); record("F322", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
