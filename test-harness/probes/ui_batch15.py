#!/usr/bin/env python3
"""Verifikation UI-Batch 15 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F146  Split-View: links die Session, rechts die gerade vom Agent bearbeitete Datei read-only. Der
        ECHTE SessionSplitView leitet die bearbeitete Datei (src/index.js) aus dem letzten Edit-Tool-Call
        ab und zeigt sie read-only neben dem Transkript. Per OCR beider Spalten nachgewiesen.
        Screenshot F146-split.png.
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


def capture(mode, dst: Path, min_w=600):
    env = {**os.environ, "CLAUDESTUDIO_UITEST": mode}
    proc = subprocess.Popen([str(APP)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wid = find_window(min_w)
        if not wid:
            return None
        time.sleep(1.0)
        raw = dst.with_suffix(".raw.png")
        sh("screencapture", "-x", "-o", f"-l{wid}", str(raw))
        srgb = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"
        r = sh("sips", "--matchToWithIntent", srgb, "relative", str(raw), "--out", str(dst))
        if r.returncode != 0 or not dst.exists():
            dst.write_bytes(raw.read_bytes())
        return dst
    finally:
        proc.terminate(); kill_app()


def ocr_halves(png: Path):
    """OCR linke + rechte Bildhälfte getrennt (Split-Spalten)."""
    im = Image.open(png).convert("RGB"); W, H = im.size
    y0, y1 = int(H * 0.06), int(H * 0.92)
    left = im.crop((int(W * 0.02), y0, int(W * 0.50), y1))
    right = im.crop((int(W * 0.50), y0, int(W * 0.98), y1))
    out = []
    for tag, c in (("L", left), ("R", right)):
        c = c.resize((c.width * 2, c.height * 2), Image.LANCZOS)
        tmp = png.with_suffix(f".{tag}.png"); c.save(tmp)
        out.append(sh("tesseract", str(tmp), "stdout", "--psm", "6").stdout.lower())
    return out


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    try:
        (EVID / "F146").mkdir(parents=True, exist_ok=True)
        png = capture("split", EVID / "F146-split.png", min_w=600)
        assert png, "Split-Fenster fehlt"
        left, right = ocr_halves(png)
        full = left + " " + right
        # Links: Session-Transkript inkl. Edit-Tool-Call
        assert "session" in left, f"linke Spalte ohne Session-Header: {left[:160]!r}"
        assert "edit" in left, f"linke Spalte ohne Edit-Tool-Call: {left[:160]!r}"
        # Rechts: bearbeitete Datei read-only + Inhalt
        assert "index.js" in right.replace(" ", ""), f"rechte Spalte ohne Dateinamen: {right[:200]!r}"
        assert "read" in right, f"rechte Spalte nicht als read-only markiert: {right[:200]!r}"
        assert ("express" in right or "delete" in right or "todos" in right), \
            f"rechte Spalte ohne Dateiinhalt: {right[:240]!r}"
        record("F146", "pass", ev("F146", "split-view.json",
               {"left_ocr": left[:200], "right_ocr": right[:240],
                "screen": "test-harness/evidence/F146-split.png"}),
               "Split-View: links Session (inkl. Edit), rechts bearbeitete Datei src/index.js read-only mit Inhalt")
    except Exception as e:
        record("F146", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
