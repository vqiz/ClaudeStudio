#!/usr/bin/env python3
"""Verifikation LLM-Batch 8 (echter Core, ECHTER claude, echtes React + node --test):

  F341  Framework-Migrations-Assistent: eine React-KLASSEN-Komponente wird vom echten claude zu einer
        FUNKTIONS-Komponente mit Hooks migriert; danach baut/läuft die Test-Suite weiterhin grün
        (gerenderte Ausgabe identisch) und die Klasse ist verschwunden.
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
    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/llm8.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": CLAUDE}) as ctx:
        c = P.Client(ctx["sock"], timeout=600)
        try:
            proj = Path(tempfile.mkdtemp(prefix="cs-f341-"))
            (proj / "package.json").write_text('{"name":"f341","type":"module","version":"1.0.0"}')
            subprocess.run(["npm", "install", "react", "react-dom", "--no-audit", "--no-fund", "--loglevel", "error"],
                           cwd=proj, capture_output=True, text=True, timeout=120)
            (proj / "greeting.mjs").write_text(
                "import React from 'react';\n"
                "export class Greeting extends React.Component {\n"
                "  constructor(props) { super(props); this.state = { count: 3 }; }\n"
                "  render() {\n"
                "    return React.createElement('div', null, `Hallo ${this.props.name}, Count: ${this.state.count}`);\n"
                "  }\n"
                "}\n")
            test_src = (
                "import { test } from 'node:test';\n"
                "import assert from 'node:assert';\n"
                "import React from 'react';\n"
                "import { renderToStaticMarkup } from 'react-dom/server';\n"
                "import { Greeting } from './greeting.mjs';\n"
                "test('greeting renders', () => {\n"
                "  const html = renderToStaticMarkup(React.createElement(Greeting, { name: 'Welt' }));\n"
                "  assert.ok(html.includes('Hallo Welt'), html);\n"
                "  assert.ok(html.includes('Count: 3'), html);\n"
                "});\n")
            (proj / "greeting.test.mjs").write_text(test_src)

            # Vorbedingung: Klassen-Komponente + Tests grün
            pre = subprocess.run(["node", "--test", "greeting.test.mjs"], cwd=proj, capture_output=True, text=True)
            assert pre.returncode == 0, f"Setup: Tests nicht grün: {pre.stdout[-300:]}{pre.stderr[-300:]}"
            assert "class Greeting" in (proj / "greeting.mjs").read_text()

            r = c.request("refactoring.migrate_component", {"cwd": str(proj), "file": "greeting.mjs"})
            src = (proj / "greeting.mjs").read_text()

            # Klasse ist weg, Funktions-Komponente mit Hook
            assert "class Greeting" not in src, "noch eine Klassen-Komponente"
            assert ("function Greeting" in src or "Greeting = (" in src or "Greeting=(" in src
                    or "=>" in src), "keine Funktions-Komponente"
            assert "useState" in src, "kein useState-Hook"
            # Verhalten erhalten: Tests weiterhin grün
            post = subprocess.run(["node", "--test", "greeting.test.mjs"], cwd=proj, capture_output=True, text=True)
            assert post.returncode == 0, f"Tests nach Migration rot: {post.stdout[-300:]}{post.stderr[-300:]}"
            # Testdatei unverändert
            assert (proj / "greeting.test.mjs").read_text() == test_src, "Testdatei wurde verändert"

            record("F341", "pass", ev("F341", "migrate-component.json",
                   {"migrated_source": src, "tests_green_after": True}),
                   "Klasse -> Funktions-Komponente mit useState migriert; Tests bleiben grün")
        except Exception as e:
            record("F341", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
