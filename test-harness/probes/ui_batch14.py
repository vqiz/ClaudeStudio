#!/usr/bin/env python3
"""Verifikation UI-Batch 14 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F147  Extended-Thinking wird als kollabierbare Sektion dargestellt; ein Button blendet den
        Denkprozess ein/aus. Die ECHTE TranscriptRow rendert ein `.thinking`-Event als DisclosureGroup:
        zugeklappt nur der "Extended Thinking"-Button, aufgeklappt der vollständige Denkprozess. Per
        OCR + Inhalts-Pixel nachgewiesen. Screenshots F147-collapsed.png / F147-expanded.png.
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


def capture(mode, dst: Path, min_w=350):
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


def crop(png: Path):
    im = Image.open(png).convert("RGB"); W, H = im.size
    return im.crop((int(W * 0.28), int(H * 0.20), int(W * 0.74), int(H * 0.80)))


def ocr(png: Path) -> str:
    c = crop(png); c = c.resize((c.width * 2, c.height * 2), Image.LANCZOS)
    tmp = png.with_suffix(".ocr.png"); c.save(tmp)
    return sh("tesseract", str(tmp), "stdout", "--psm", "6").stdout.lower()


def dark_px(png: Path) -> int:
    im = crop(png); px = im.load(); W, H = im.size
    # Text-/Inhalts-Pixel: grau (~140) + lila (~175) Schrift, aber nicht der helle Block-Hintergrund.
    return sum(1 for y in range(0, H, 2) for x in range(0, W, 2) if max(px[x, y]) < 198)


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    try:
        (EVID / "F147").mkdir(parents=True, exist_ok=True)
        col = capture("think-collapsed", EVID / "F147-collapsed.png", min_w=350)
        exp = capture("think-expanded", EVID / "F147-expanded.png", min_w=350)
        assert col and exp, "Thinking-Fenster fehlt"
        t_col = ocr(col); t_exp = ocr(exp)
        d_col = dark_px(col); d_exp = dark_px(exp)
        # Beide: der "Extended Thinking"-Button/Sektion ist sichtbar.
        assert "thinking" in t_col, f"Thinking-Sektion fehlt (zugeklappt): {t_col[:160]!r}"
        # Zugeklappt: der Denkprozess-Text ist NICHT sichtbar.
        assert "index.js" not in t_col and "204" not in t_col, f"zugeklappt zeigt schon den Denkprozess: {t_col[:200]!r}"
        # Aufgeklappt: der Denkprozess-Text ist sichtbar.
        assert ("index.js" in t_exp or "delete" in t_exp or "204" in t_exp), f"aufgeklappt ohne Denkprozess: {t_exp[:240]!r}"
        assert d_exp > d_col * 1.5, f"aufgeklappt nicht inhaltsreicher ({d_exp} vs {d_col}px)"
        record("F147", "pass", ev("F147", "thinking-section.json",
               {"collapsed_ocr": t_col[:200], "expanded_ocr": t_exp[:260],
                "collapsed_dark_px": d_col, "expanded_dark_px": d_exp,
                "screens": ["test-harness/evidence/F147-collapsed.png", "test-harness/evidence/F147-expanded.png"]}),
               "Extended-Thinking-Sektion: zugeklappt nur Button, aufgeklappt voller Denkprozess sichtbar")
    except Exception as e:
        record("F147", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
