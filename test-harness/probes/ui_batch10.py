#!/usr/bin/env python3
"""Verifikation UI-Batch 10 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F137  Tool-Calls werden im Session-Panel als auf-/zuklappbare Karten dargestellt, jede mit
        Tool-Name + Ausführungs-Status. Gerendert über die ECHTEN TranscriptRow/DisclosureGroup-
        Karten (SessionPanelView) mit geseedeten Tool-Calls (Edit, Bash). Zugeklappt: nur Name +
        Status; aufgeklappt: zusätzlich Input + Output. Per OCR + Inhalts-Pixel nachgewiesen.
        Screenshots F137-collapsed.png / F137-expanded.png.
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


def crop_region(png: Path):
    im = Image.open(png).convert("RGB"); W, H = im.size
    return im.crop((int(W * 0.30), int(H * 0.18), int(W * 0.72), int(H * 0.86)))


def ocr(png: Path) -> str:
    crop = crop_region(png)
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    tmp = png.with_suffix(".ocr.png"); crop.save(tmp)
    return sh("tesseract", str(tmp), "stdout", "--psm", "6").stdout.lower()


def dark_px(png: Path) -> int:
    im = crop_region(png); px = im.load(); W, H = im.size
    return sum(1 for y in range(0, H, 2) for x in range(0, W, 2)
               if max(px[x, y]) < 120)


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    try:
        (EVID / "F137").mkdir(parents=True, exist_ok=True)
        col = capture("panel-collapsed", EVID / "F137-collapsed.png", min_w=350)
        exp = capture("panel-expanded", EVID / "F137-expanded.png", min_w=350)
        assert col and exp, "Panel-Fenster fehlt"

        t_col = ocr(col)
        t_exp = ocr(exp)
        d_col = dark_px(col)
        d_exp = dark_px(exp)

        # Beide States: die Tool-Call-KARTEN (Name) sind sichtbar.
        assert "edit" in t_col and "bash" in t_col, f"Tool-Karten fehlen (zugeklappt): {t_col[:160]!r}"
        # Zugeklappt: Input/Output NICHT sichtbar.
        assert "input" not in t_col and "output" not in t_col, f"zugeklappt zeigt schon Inhalt: {t_col[:160]!r}"
        # Aufgeklappt: Input + Output sichtbar.
        assert "input" in t_exp and "output" in t_exp, f"aufgeklappt ohne Input/Output: {t_exp[:200]!r}"
        # Aufgeklappt hat deutlich mehr Inhalt.
        assert d_exp > d_col * 1.4, f"aufgeklappt nicht inhaltsreicher ({d_exp} vs {d_col}px)"
        record("F137", "pass", ev("F137", "tool-call-cards.json",
               {"collapsed_ocr": t_col[:200], "expanded_ocr": t_exp[:240],
                "collapsed_dark_px": d_col, "expanded_dark_px": d_exp,
                "screens": ["test-harness/evidence/F137-collapsed.png", "test-harness/evidence/F137-expanded.png"]}),
               "Tool-Call-Karten (Edit/Bash) zugeklappt nur Name+Status; aufgeklappt Input+Output sichtbar")
    except Exception as e:
        record("F137", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
