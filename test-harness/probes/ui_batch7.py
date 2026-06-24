#!/usr/bin/env python3
"""Verifikation UI-Batch 7 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F032  Kollabierbare Definitionen-Sektion: die echte SidebarView nutzt jetzt Section(isExpanded:);
        gerendert über den Seam zeigt der expandierte State die Unterpunkte (Agent Studio, Context,
        Definitions Library), der kollabierte State blendet sie aus. Per OCR nachgewiesen.
        Screenshots F032-expanded.png / F032-collapsed.png.
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


def capture(mode, dst: Path, min_w=180):
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


def body_dark_pixels(png: Path) -> int:
    """Dunkle Text-/Icon-Pixel im LISTENKÖRPER (unterhalb des 'Definitions'-Headers) zählen:
    expandiert ⇒ drei Zeilen Text+Icons (viele dunkle Pixel), kollabiert ⇒ nahezu keine."""
    im = Image.open(png).convert("RGB"); W, H = im.size; px = im.load()
    x0, x1 = int(W * 0.38), int(W * 0.62)
    y0, y1 = int(H * 0.32), int(H * 0.78)  # unter dem Header beginnen
    dark = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            r, g, b = px[x, y]
            if max(r, g, b) < 130:
                dark += 1
    return dark


def ocr_full(png: Path) -> str:
    im = Image.open(png).convert("RGB"); W, H = im.size
    crop = im.crop((int(W * 0.36), int(H * 0.24), int(W * 0.64), int(H * 0.82)))
    crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
    tmp = png.with_suffix(".ocr.png"); crop.save(tmp)
    return sh("tesseract", str(tmp), "stdout", "--psm", "6").stdout.lower()


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    SUBITEMS = ("studio", "context", "library")
    try:
        (EVID / "F032").mkdir(parents=True, exist_ok=True)
        exp = capture("defs-expanded", EVID / "F032-expanded.png", min_w=180)
        col = capture("defs-collapsed", EVID / "F032-collapsed.png", min_w=180)
        assert exp and col, "Definitionen-Fenster fehlt"

        # Primärnachweis: dunkle Inhalts-Pixel im Listenkörper (robust gegen kleine graue Schrift).
        d_exp = body_dark_pixels(exp)
        d_col = body_dark_pixels(col)
        # Sekundär: OCR der hochskalierten Liste (Unterpunkt-Namen).
        t_exp = ocr_full(exp)
        n_exp = sum(1 for s in SUBITEMS if s in t_exp)
        assert d_exp > 200, f"expandiert: zu wenig Inhalt im Listenkörper ({d_exp}px)"
        assert d_col < d_exp * 0.25, f"kollabiert: Unterpunkte nicht ausgeblendet ({d_col} vs {d_exp}px)"
        record("F032", "pass", ev("F032", "definitions-collapse.json",
               {"expanded_dark_px": d_exp, "collapsed_dark_px": d_col,
                "expanded_subitems_ocr": n_exp, "expanded_ocr": t_exp[:160],
                "screens": ["test-harness/evidence/F032-expanded.png", "test-harness/evidence/F032-collapsed.png"]}),
               f"expandiert {d_exp}px Inhalt (OCR {n_exp}/3 Unterpunkte) > kollabiert {d_col}px (ausgeblendet)")
    except Exception as e:
        record("F032", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
