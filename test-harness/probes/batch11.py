#!/usr/bin/env python3
"""Verifikation Build-Batch 11: statische Code-Analyse + Doku gegen den echten Core.

Neu (router.rs): codeq.dead_code/duplicates/complexity, perf.compare,
docs.generate (Markdown aus Kommentaren+Signaturen), docs.arch_diagram (Mermaid aus
Imports), i18n.extract (hartkodierte Strings -> t('key')). Echte Dateien, echter Core.
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


def make_project():
    d = Path(tempfile.mkdtemp(prefix="cs-b11-proj-"))
    (d / "used.ts").write_text(
        "export const usedThing = 1\n"
        "export function unusedHelper() { return 99 }\n"
    )
    (d / "main.ts").write_text(
        "import { usedThing } from './used'\n"
        "/**\n * Adds two numbers together.\n */\n"
        "export function addNumbers(a, b) {\n"
        "  if (a > 0) { return a + b }\n"
        "  for (let i = 0; i < b; i++) { if (i && a) { console.log(i) } }\n"
        "  return usedThing\n"
        "}\n"
    )
    # duplicated block across two files
    block = "const x = compute(a)\nconst y = compute(b)\nreturn x + y + z\nconsole.log('done')\n"
    (d / "dupA.js").write_text("function f1() {\n" + block + "}\n")
    (d / "dupB.js").write_text("function f2() {\n" + block + "}\n")
    return d


def main():
    proj = make_project()
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b11.log")) as ctx:
        c = P.Client(ctx["sock"])

        # F318 dead code
        try:
            r = c.request("codeq.dead_code", {"cwd": str(proj)})
            names = {x["name"] for x in r["dead_exports"]}
            assert "unusedHelper" in names and "usedThing" not in names
            record("F318", "pass", ev("F318", "dead-code.json", r), "ungenutzten Export erkannt, genutzten nicht")
        except Exception as e:
            record("F318", "fail", note=str(e))

        # F319 duplicates
        try:
            r = c.request("codeq.duplicates", {"cwd": str(proj), "window": 4})
            cross = [d for d in r["duplicates"] if any("dupA" in loc for loc in d["locations"]) and any("dupB" in loc for loc in d["locations"])]
            assert cross, f"no cross-file duplicate found: {r['duplicates']}"
            record("F319", "pass", ev("F319", "duplicates.json", r), "kopierten Block über Dateien hinweg erkannt")
        except Exception as e:
            record("F319", "fail", note=str(e))

        # F320 complexity
        try:
            r = c.request("codeq.complexity", {"cwd": str(proj)})
            main_c = next(f for f in r["files"] if f["file"].endswith("main.ts"))
            used_c = next(f for f in r["files"] if f["file"].endswith("used.ts"))
            assert main_c["complexity"] > used_c["complexity"] and main_c["complexity"] > 1
            record("F320", "pass", ev("F320", "complexity.json", r), "zyklomatische Komplexität je Datei berechnet")
        except Exception as e:
            record("F320", "fail", note=str(e))

        # F325 perf regression
        try:
            reg = c.request("perf.compare", {"baseline_ms": 100, "current_ms": 130, "threshold_pct": 10})
            ok = c.request("perf.compare", {"baseline_ms": 100, "current_ms": 105, "threshold_pct": 10})
            assert reg["regression"] is True and ok["regression"] is False
            record("F325", "pass", ev("F325", "perf.json", {"regression": reg, "ok": ok}),
                   "Performance-Regression erkannt (+30% > 10%)")
        except Exception as e:
            record("F325", "fail", note=str(e))

        # F332 auto docs
        try:
            r = c.request("docs.generate", {"cwd": str(proj)})
            assert r["documented"] >= 1 and "addNumbers" in r["markdown"] and "Adds two numbers" in r["markdown"]
            record("F332", "pass", ev("F332", "docs.md", r["markdown"]), "Markdown-Doku aus Kommentaren+Signaturen")
        except Exception as e:
            record("F332", "fail", note=str(e))

        # F334 architecture diagram
        try:
            r = c.request("docs.arch_diagram", {"cwd": str(proj)})
            assert "graph TD" in r["mermaid"] and "main --> used" in r["mermaid"] and r["edges"] >= 1
            record("F334", "pass", ev("F334", "arch.mmd", r["mermaid"]), "Mermaid-Komponentendiagramm aus Imports")
        except Exception as e:
            record("F334", "fail", note=str(e))

        # F344 i18n extraction
        try:
            r = c.request("i18n.extract", {"content": 'return <button title="Save now">{"Hello world"}</button>'})
            assert r["extracted"] >= 1 and "t('key_0')" in r["transformed"]
            assert any(v in ("Save now", "Hello world") for v in r["catalog"].values())
            record("F344", "pass", ev("F344", "i18n.json", r), "hartkodierte Strings -> t('key') + Katalog")
        except Exception as e:
            record("F344", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
