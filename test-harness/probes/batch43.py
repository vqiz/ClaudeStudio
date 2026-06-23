#!/usr/bin/env python3
"""Verifikation Build-Batch 43 (echter Core, kein Mock):

  F210  Task-Builder mit sechs Tab-Sektionen (Grunddaten/Agent-Config/Inputs/Workflow/Output/
        Schedule): ein eigener Task mit allen Pflichtfeldern wird gespeichert und erscheint
        danach in der Task-Liste. Unvollständige Tasks (fehlende Sektion) werden abgelehnt.
"""
from __future__ import annotations
import json, sys
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
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b43.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=15)
        try:
            # die sechs Builder-Tabs als Task-Sektionen
            task = {
                "name": "Mein Rechnungs-Audit",                                    # Grunddaten
                "description": "Prüft Rechnungen auf Pflichtangaben",
                "icon": "wand.and.stars",
                "agent": {"model": "claude-sonnet-4-6", "allowed_tools": ["Read", "Grep"]},  # Agent-Config
                "inputs": [{"key": "pfad", "label": "Projektpfad", "type": "text", "required": True}],  # Inputs
                "workflow": {"steps": [{"prompt": "Analysiere {{pfad}} auf Pflichtangaben"}]},  # Workflow
                "output": {"format": "markdown", "destination": "report"},          # Output
                "schedule": {"type": "manual"},                                     # Schedule
            }
            r = c.request("tasks.save", {"task": task})
            assert r["ok"] and r["id"] == "mein-rechnungs-audit"
            assert set(r["sections"]) == {"name", "agent", "inputs", "workflow", "output", "schedule"}

            # erscheint in der Task-Liste
            tasks = c.request("tasks.list", {})["tasks"]
            assert any(t["name"] == "Mein Rechnungs-Audit" for t in tasks), [t["name"] for t in tasks]

            # gespeicherte Datei trägt alle sechs Sektionen
            saved = json.loads(Path(r["path"]).read_text())
            for sec in ("name", "agent", "inputs", "workflow", "output", "schedule"):
                assert sec in saved, f"Sektion fehlt in Datei: {sec}"
            assert saved["agent"]["model"] == "claude-sonnet-4-6"

            # unvollständiger Task (ohne 'schedule') wird abgelehnt
            rejected = False
            try:
                c.request("tasks.save", {"task": {"name": "Unvollständig", "agent": {}, "inputs": [],
                                                  "workflow": {}, "output": {}}})
            except P.RemoteError:
                rejected = True
            assert rejected

            record("F210", "pass", ev("F210", "task-builder-save.json",
                   {"saved_id": r["id"], "sections": r["sections"],
                    "in_list": True, "incomplete_rejected": rejected}),
                   "6-Sektionen-Task gespeichert + in Liste; unvollständiger Task abgelehnt")
        except Exception as e:
            record("F210", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
