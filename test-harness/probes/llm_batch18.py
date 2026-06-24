#!/usr/bin/env python3
"""Verifikation LLM-Batch 18 (echter Core, ECHTER claude Plan-Mode):

  F138  Plan-Mode-Visualizer: im Plan-Modus liefert der echte claude für 'Füge einen DELETE-Endpoint
        hinzu' einen strukturierten Plan (>=3 konkrete Schritte), der als Liste dargestellt werden kann.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
CLAUDE = os.path.expanduser("~/.local/bin/claude")
GENV = {**os.environ, "GIT_AUTHOR_NAME": "B", "GIT_AUTHOR_EMAIL": "b@b",
        "GIT_COMMITTER_NAME": "B", "GIT_COMMITTER_EMAIL": "b@b"}
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def main():
    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/llm18.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": CLAUDE}) as ctx:
        c = P.Client(ctx["sock"], timeout=420)
        try:
            proj = Path(tempfile.mkdtemp(prefix="cs-f138-"))
            (proj / "index.js").write_text(
                "const express = require('express');\nconst app = express();\n"
                "const todos = [{id:1,title:'x'}];\n"
                "app.get('/todos', (req,res)=>res.json(todos));\napp.listen(3000);\n")
            r = c.request("agents.plan_mode", {
                "cwd": str(proj),
                "task": "Füge einen DELETE-Endpoint /todos/:id zur todo-api hinzu"})
            assert r["plan"].strip(), "kein Plan erzeugt"
            assert r["step_count"] >= 3, f"Plan nicht strukturiert genug ({r['step_count']} Schritte)"
            blob = r["plan"].lower()
            assert any(k in blob for k in ("delete", "endpoint", "route", "/todos", "id")), \
                "Plan nicht aufgabenbezogen"
            record("F138", "pass", ev("F138", "plan-mode.json",
                   {"step_count": r["step_count"], "steps": r["steps"][:8], "plan_excerpt": r["plan"][:500]}),
                   f"Plan-Mode lieferte strukturierten Plan ({r['step_count']} Schritte, DELETE-Endpoint)")
        except Exception as e:
            record("F138", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
