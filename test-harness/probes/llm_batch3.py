#!/usr/bin/env python3
"""Verifikation LLM-Batch 3 (echter Core, ECHTER claude-Orchestrator):

  F120  Strukturiertes Team: der echte claude-Orchestrator zerlegt die Aufgabe 'Health-Endpoint zu
        todo-api hinzufügen' in konkrete Subtasks (>=3, je mit Titel) und weist sie den zwei Workern
        round-robin zu.
"""
from __future__ import annotations
import json, os, sys
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
    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/llm3.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": CLAUDE}) as ctx:
        c = P.Client(ctx["sock"], timeout=300)
        try:
            r = c.request("agents.decompose_task", {
                "task": "Füge einen Health-Check-Endpoint /health zur todo-api (Node/Express) hinzu, inkl. Test",
                "workers": ["design-w", "logic-w"]})
            sub = r["subtasks"]
            assert r["subtask_count"] >= 3, f"nur {r['subtask_count']} Subtasks"
            assert all(isinstance(s, dict) and str(s.get("title", "")).strip() for s in sub), "Subtask ohne Titel"
            # Subtasks sind aufgabenbezogen
            blob = json.dumps(sub).lower()
            assert any(k in blob for k in ("health", "endpoint", "route", "/health", "test", "handler")), \
                "Subtasks nicht aufgabenbezogen"
            # round-robin auf beide Worker verteilt
            assigned = {a["worker"] for a in r["assignments"]}
            assert {"design-w", "logic-w"} <= assigned, f"Worker-Zuweisung: {assigned}"
            record("F120", "pass", ev("F120", "decompose.json",
                   {"subtask_count": r["subtask_count"],
                    "titles": [s.get("title") for s in sub], "assignments": r["assignments"]}),
                   f"Orchestrator erzeugte {r['subtask_count']} Subtasks, verteilt auf beide Worker")
        except Exception as e:
            record("F120", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
