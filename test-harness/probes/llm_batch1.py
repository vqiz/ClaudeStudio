#!/usr/bin/env python3
"""Verifikation LLM-Batch 1 (echter Core, ECHTER claude-Agent, echtes node --test, kein Mock):

  F321  Test-Generierungs-Agent: für eine ungetestete Funktion schreibt der echte claude-Agent
        autonom eine Testdatei und führt sie aus; die generierten Tests laufen grün UND decken
        die Funktion ab (echte Coverage > 60%).

Nutzt den echten `claude` (~/.local/bin/claude). Der Core läuft mit dem ECHTEN HOME, damit claude
seine Auth findet.
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
    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/llm1.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": CLAUDE}) as ctx:
        c = P.Client(ctx["sock"], timeout=420)  # echter Agentenlauf kann Minuten dauern
        try:
            proj = Path(tempfile.mkdtemp(prefix="cs-f321-"))
            # ESM-Modul mit drei ungetesteten Funktionen
            (proj / "package.json").write_text('{"name":"f321","type":"module"}')
            (proj / "math.mjs").write_text(
                "export function add(a, b) { return a + b; }\n"
                "export function sub(a, b) { return a - b; }\n"
                "export function mul(a, b) { return a * b; }\n")

            r = c.request("testing.generate_tests", {"cwd": str(proj), "target": "math.mjs"})
            assert r["test_files"], f"kein Testfile erzeugt (log: {r.get('agent_log_tail','')[:200]})"
            # die vom Agenten erzeugte Testdatei (math.mjs selbst ausschließen)
            tf = next((f for f in r["test_files"] if f != "math.mjs"), r["test_files"][0])

            # 1) Tests laufen grün
            run = subprocess.run(["node", "--test", tf], cwd=proj, capture_output=True, text=True)
            assert run.returncode == 0, f"Tests rot: {run.stdout[-400:]}{run.stderr[-200:]}"

            # 2) echte Coverage der Zielfunktionen
            cov = c.request("coverage.measure", {"cwd": str(proj),
                "command": ("node --test --experimental-test-coverage --test-reporter=lcov "
                            f"--test-reporter-destination=coverage.lcov {tf}"),
                "lcov": "coverage.lcov"})
            mathcov = next((m for m in cov["modules"] if m["file"].endswith("math.mjs")), None)
            assert mathcov and mathcov["percent"] >= 60, f"Coverage math.mjs: {mathcov}"

            record("F321", "pass", ev("F321", "test-generation.json",
                   {"test_file": tf, "tests_green": True,
                    "math_coverage_percent": mathcov["percent"],
                    "agent_log_tail": r.get("agent_log_tail", "")[:300]}),
                   f"claude-Agent schrieb {tf}; Tests grün; math.mjs-Coverage {mathcov['percent']}%")
        except Exception as e:
            record("F321", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
