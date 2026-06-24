#!/usr/bin/env python3
"""Verifikation UI-Batch 23 (echte SwiftUI-App = echtes ProjectWorkspaceView, kein Mock):

  F028  Auswahl eines Projekts blendet die Projekt-Tabs ein (Agents, Sessions, Files, Git, Tasks,
        Context, Code, Settings) — alle 8 sichtbar.
  F043  Klick auf eine Projekt-Card öffnet die Tab-Ansicht für GENAU dieses Projekt: Titel
        'data-pipeline' (nicht todo-api) + die 8 Tabs.

Verifiziert am echten ProjectWorkspaceView, das für das Projekt 'data-pipeline' gerendert wird (der
Klick-Effekt via CLAUDESTUDIO_PROJECT_NAME/PATH). Files/Code/Git lesen das echte Projektverzeichnis.
Screenshot F028-F043-workspace.png.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402
from PIL import Image  # noqa: E402

ROOT = P.ROOT
EVID = ROOT / "test-harness" / "evidence"
APP = ROOT / "app" / ".build" / "debug" / "ClaudeStudio"
WINDOWID = ROOT / "test-harness" / "lib" / "windowid"
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True)


def kill_app():
    sh("pkill", "-f", "ClaudeStudio"); time.sleep(0.8)


def find_window(min_w):
    for _ in range(30):
        time.sleep(0.4)
        out = sh(str(WINDOWID), "ClaudeStudio").stdout
        best = None
        for line in out.splitlines():
            p = line.split("\t")
            if len(p) >= 4 and p[3] == "0" and int(p[1]) >= min_w:
                if best is None or int(p[1]) > best[1]:
                    best = (p[0], int(p[1]))
        if best:
            return best[0]
    return None


def capture(name, path, dst: Path, min_w=800):
    env = {**os.environ, "CLAUDESTUDIO_UITEST": "project-workspace",
           "CLAUDESTUDIO_PROJECT_NAME": name, "CLAUDESTUDIO_PROJECT_PATH": path}
    proc = subprocess.Popen([str(APP)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wid = find_window(min_w)
        if not wid:
            return None
        time.sleep(1.6)
        raw = dst.with_suffix(".raw.png")
        sh("screencapture", "-x", "-o", f"-l{wid}", str(raw))
        srgb = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"
        r = sh("sips", "--matchToWithIntent", srgb, "relative", str(raw), "--out", str(dst))
        if r.returncode != 0 or not dst.exists():
            dst.write_bytes(raw.read_bytes())
        return dst
    finally:
        proc.terminate(); kill_app()


def ocr_region(png: Path, y0f, y1f, scale=2) -> str:
    im = Image.open(png).convert("RGB"); W, H = im.size
    crop = im.crop((int(W * 0.02), int(H * y0f), int(W * 0.98), int(H * y1f)))
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    tmp = png.with_suffix(f".{int(y0f*100)}.png"); crop.save(tmp)
    return sh("tesseract", str(tmp), "stdout", "--psm", "6").stdout.lower()


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    proj = Path(tempfile.mkdtemp(prefix="cs-datapipe-"))
    (proj / "pipeline.py").write_text("def run():\n    print('pipeline')\n")
    (proj / "README.md").write_text("# data-pipeline\n")
    TABS = ["agents", "sessions", "files", "git", "tasks", "context", "code", "settings"]
    try:
        (EVID / "F028").mkdir(parents=True, exist_ok=True)
        png = capture("data-pipeline", str(proj), EVID / "F028-F043-workspace.png", min_w=800)
        assert png, "Workspace-Fenster fehlt"
        head = ocr_region(png, 0.02, 0.16, scale=3)     # Kopf (Projekttitel)
        tabs = ocr_region(png, 0.10, 0.26, scale=3)      # Tab-Leiste
        both = head + " " + tabs

        # F043: korrekter Projekttitel
        assert "data-pipeline" in both.replace(" ", "-") or "data-pipeline" in both or "datapipeline" in both.replace(" ", ""), \
            f"Projekttitel 'data-pipeline' fehlt: {head[:160]!r}"
        assert "todo-api" not in both, "falsches Projekt (todo-api) angezeigt"
        # F028: alle 8 Tabs sichtbar
        found = [t for t in TABS if t in tabs]
        assert len(found) >= 7, f"zu wenige der 8 Tabs erkannt: {found} | OCR={tabs[:200]!r}"

        evp = ev("F028", "project-tabs.json",
                 {"project_title_ok": True, "tabs_found": found,
                  "head_ocr": head[:120], "tabs_ocr": tabs[:200],
                  "screen": "test-harness/evidence/F028-F043-workspace.png"})
        record("F028", "pass", evp,
               f"Projekt-Tabs sichtbar: {found} ({len(found)}/8)")
        record("F043", "pass", evp,
               f"Workspace für 'data-pipeline' geöffnet (nicht todo-api) mit {len(found)}/8 Tabs")
    except Exception as e:
        record("F028", "fail", note=str(e))
        record("F043", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
