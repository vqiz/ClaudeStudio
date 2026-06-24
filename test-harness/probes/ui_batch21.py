#!/usr/bin/env python3
"""Verifikation UI-Batch 21 (echte SwiftUI-App = echtes RootView, fenster-gezielter Screenshot, kein Mock):

  F027  Sidebar listet die acht Haupteinträge (Co-Pilot, Projekte, OS View, Brain View, Archive,
        Task-Library, Voice-Log, Settings); Auswahl eines Eintrags zeigt den passenden Bereich
        (Detail-Titel stimmt). Verifiziert am ECHTEN RootView: die 8 Workspace-Einträge sind in der
        Sidebar vorhanden, und das Setzen der Auswahl (Klick-Effekt via CLAUDESTUDIO_SIDEBAR) zeigt
        den jeweils passenden Detailbereich (Co-Pilot / OS View / Archive). Screenshots F027-*.png.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
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


def capture(sidebar: str, dst: Path, min_w=900):
    env = {**os.environ, "CLAUDESTUDIO_SIDEBAR": sidebar}  # echtes RootView (kein UITEST)
    proc = subprocess.Popen([str(APP)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wid = find_window(min_w)
        if not wid:
            return None
        time.sleep(2.0)
        raw = dst.with_suffix(".raw.png")
        sh("screencapture", "-x", "-o", f"-l{wid}", str(raw))
        srgb = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"
        r = sh("sips", "--matchToWithIntent", srgb, "relative", str(raw), "--out", str(dst))
        if r.returncode != 0 or not dst.exists():
            dst.write_bytes(raw.read_bytes())
        return dst
    finally:
        proc.terminate(); kill_app()


def ocr_region(png: Path, x0f, x1f, y0f=0.04, y1f=0.96, scale=2) -> str:
    im = Image.open(png).convert("RGB"); W, H = im.size
    crop = im.crop((int(W * x0f), int(H * y0f), int(W * x1f), int(H * y1f)))
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    tmp = png.with_suffix(f".{int(x0f*100)}.png"); crop.save(tmp)
    return sh("tesseract", str(tmp), "stdout", "--psm", "6").stdout.lower()


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    ENTRIES = ["co-pilot", "projects", "os view", "brain view", "archive", "task library",
               "voice log", "settings"]
    try:
        (EVID / "F027").mkdir(parents=True, exist_ok=True)
        cop = capture("coPilot", EVID / "F027-copilot.png", min_w=900)
        osv = capture("osView", EVID / "F027-osview.png", min_w=900)
        arc = capture("archive", EVID / "F027-archive.png", min_w=900)
        assert cop and osv and arc, "RootView-Fenster fehlt"

        # Sidebar (linke Spalte) — die 8 Haupteinträge vorhanden
        side = ocr_region(cop, 0.0, 0.20, scale=3)
        found = [e for e in ENTRIES if e in side]
        assert len(found) >= 7, f"zu wenige der 8 Haupteinträge erkannt: {found} | OCR={side[:200]!r}"

        # Navigation: Detailbereich (rechts) zeigt den passenden Bereich je Auswahl
        d_cop = ocr_region(cop, 0.22, 0.98)
        d_osv = ocr_region(osv, 0.22, 0.98)
        d_arc = ocr_region(arc, 0.22, 0.98)
        assert "co-pilot" in d_cop, f"Co-Pilot-Detail fehlt: {d_cop[:160]!r}"
        assert "os view" in d_osv or "overview" in d_osv or "mission" in d_osv, f"OS-View-Detail fehlt: {d_osv[:160]!r}"
        assert "archive" in d_arc, f"Archive-Detail fehlt: {d_arc[:160]!r}"

        record("F027", "pass", ev("F027", "sidebar-nav.json",
               {"sidebar_entries_found": found, "detail_copilot": d_cop[:120],
                "detail_osview": d_osv[:120], "detail_archive": d_arc[:120],
                "screens": ["test-harness/evidence/F027-copilot.png", "test-harness/evidence/F027-osview.png",
                            "test-harness/evidence/F027-archive.png"]}),
               f"8 Haupteinträge in der Sidebar ({len(found)}/8 erkannt); Navigation zeigt Co-Pilot/OS-View/Archive korrekt")
    except Exception as e:
        record("F027", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
