#!/usr/bin/env python3
"""Verifikation F043 — Klick auf eine Projekt-Card öffnet die Tabs GENAU dieses Projekts.

Im Hub setzt der Klick auf eine Card `selectedProjectID`; `selectedProject = projects.first{ id }`
löst daraus das Projekt auf, und die rechte Spalte zeigt `ProjectWorkspaceView(project:)` (siehe
ProjectsView.swift). Die Klick-Geste ist — wie bei allen UI-Features — durch das Setzen von
selectedProjectID ersetzt; verifiziert wird die ECHTE Auflösungslogik + die geöffnete Tab-Ansicht:

  - Seam (CLAUDESTUDIO_RENDER_CARDOPEN) legt zwei echte Projekte an (todo-api, data-pipeline), klickt
    die data-pipeline-Card (selectedProjectID = deren id) und liest appState.selectedProject.
  - resolved.txt MUSS „data-pipeline" sein (nicht todo-api) — die richtige Card wurde geöffnet.
  - F043-open.png zeigt den Titel „data-pipeline" + alle 8 Tabs (Agents…Settings), gerendert aus der
    REALEN Quelle ProjectWorkspaceView.Tab.allCases.
  - Quell-Check: ProjectWorkspaceView rendert genau über diese 8 Tabs (switch über Tab.allCases).
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
EVID = ROOT / "test-harness" / "evidence"
APP = ROOT / "app" / ".build" / "debug" / "ClaudeStudio"
PWV = ROOT / "app" / "Sources" / "ClaudeStudio" / "Views" / "ProjectWorkspaceView.swift"
TABS = ["Agents", "Sessions", "Files", "Git", "Tasks", "Context", "Code", "Settings"]
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def ocr(png: Path) -> str:
    return subprocess.run(["tesseract", str(png), "stdout", "--psm", "6"],
                          capture_output=True, text=True).stdout.replace("\n", " ")


def main():
    if not APP.exists():
        print(json.dumps({"results": {"F043": {"status": "fail", "note": "App nicht gebaut"}}})); return
    try:
        outdir = EVID / "F043"
        outdir.mkdir(parents=True, exist_ok=True)
        for stale in ("F043-open.png", "resolved.txt", "workspace-real.png"):
            (outdir / stale).unlink(missing_ok=True)

        subprocess.run([str(APP)], capture_output=True, timeout=30,
                       env={**os.environ, "CLAUDESTUDIO_RENDER_CARDOPEN": str(outdir)})

        # 1) Die ECHTE Auflösungslogik hat das angeklickte Projekt geöffnet (data-pipeline, NICHT todo-api).
        resolved = (outdir / "resolved.txt").read_text().strip() if (outdir / "resolved.txt").exists() else ""
        assert resolved == "data-pipeline", f"Klick öffnete falsches Projekt: {resolved!r}"

        # 2) Screenshot: korrekter Titel + alle 8 Tabs.
        png = outdir / "F043-open.png"
        assert png.exists(), "F043-open.png fehlt"
        txt = ocr(png)
        low = txt.lower()
        assert "data-pipeline" in low and "todo-api" not in low, f"falscher Projekttitel im Screenshot: {txt!r}"
        missing = [t for t in TABS if t.lower() not in low]
        assert not missing, f"Tabs fehlen im Screenshot {missing}: {txt!r}"

        # 3) Quell-Check: ProjectWorkspaceView rendert über genau diese 8 Tabs.
        src = PWV.read_text()
        assert "case agents, sessions, files, git, tasks, context, code, settings" in src, "Tab-Enum geändert"
        for t in TABS:
            assert f'case .{t.lower()}:' in src, f"ProjectWorkspaceView rendert Tab {t} nicht (switch)"

        record("F043", "pass", ev("F043", "card-open.json", {
            "resolved_project": resolved,
            "ocr": txt[:240], "tabs_found": TABS,
            "screen": "test-harness/evidence/F043/F043-open.png",
            "workspace_real": "test-harness/evidence/F043/workspace-real.png",
        }), "Klick auf data-pipeline-Card öffnet (echte selectedProject-Auflösung) die 8-Tab-Ansicht "
            "für data-pipeline (nicht todo-api)")
    except Exception as e:
        import traceback
        record("F043", "fail", note=f"{e} | {traceback.format_exc()[-300:]}")

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
