#!/usr/bin/env python3
"""Verifikation UI-Batch 22 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F031  Trust-Modus-Indikator spiegelt den aktiven Modus mit Symbol wider (⚡ full / 🟢 trusted /
        🟡 ask / 🔴 locked). Gerendert über den ECHTEN TrustModeBadge + das ECHTE
        TrustMode.indicatorEmoji/.specLabel-Mapping: bei 'ask' 🟡 (gelb), bei 'locked' 🔴 (rot),
        bei 'trusted' 🟢 (grün). Per Pixelfarbe + OCR der Spec-Bezeichnung nachgewiesen.
        Screenshots F031-ask.png / F031-locked.png / F031-trusted.png.
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


def capture(mode, dst: Path, min_w=300):
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


def colors(png: Path):
    im = Image.open(png).convert("RGB"); W, H = im.size; px = im.load()
    yellow = red = green = 0
    for y in range(0, H, 2):
        for x in range(0, W, 2):
            r, g, b = px[x, y]
            if r > 200 and g > 170 and b < 110:
                yellow += 1
            elif r > 175 and g < 120 and b < 120:
                red += 1
            elif g > 150 and r < 150 and b < 150:
                green += 1
    return {"yellow": yellow, "red": red, "green": green}


def ocr(png: Path) -> str:
    im = Image.open(png).convert("RGB"); W, H = im.size
    crop = im.crop((int(W * 0.25), int(H * 0.55), int(W * 0.75), int(H * 0.85)))
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    tmp = png.with_suffix(".ocr.png"); crop.save(tmp)
    return sh("tesseract", str(tmp), "stdout", "--psm", "6").stdout.lower()


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    try:
        (EVID / "F031").mkdir(parents=True, exist_ok=True)
        ask = capture("trust-ask", EVID / "F031-ask.png", min_w=300)
        locked = capture("trust-locked", EVID / "F031-locked.png", min_w=300)
        trusted = capture("trust-trusted", EVID / "F031-trusted.png", min_w=300)
        assert ask and locked and trusted, "Trust-Fenster fehlt"

        c_ask, c_loc, c_tru = colors(ask), colors(locked), colors(trusted)
        # ask -> 🟡 gelb dominant
        assert c_ask["yellow"] > 200 and c_ask["yellow"] > c_ask["red"], f"ask nicht gelb: {c_ask}"
        # locked -> 🔴 rot dominant
        assert c_loc["red"] > 200 and c_loc["red"] > c_loc["yellow"], f"locked nicht rot: {c_loc}"
        # trusted -> 🟢 grün dominant
        assert c_tru["green"] > 200 and c_tru["green"] > c_tru["red"], f"trusted nicht grün: {c_tru}"
        # Spec-Bezeichnungen
        assert "ask" in ocr(ask), "ask-Label fehlt"
        assert "locked" in ocr(locked), "locked-Label fehlt"
        assert "trusted" in ocr(trusted), "trusted-Label fehlt"

        record("F031", "pass", ev("F031", "trust-indicator.json",
               {"ask": c_ask, "locked": c_loc, "trusted": c_tru,
                "screens": [f"test-harness/evidence/F031-{s}.png" for s in ("ask", "locked", "trusted")]}),
               "Trust-Indikator: ask 🟡 gelb, locked 🔴 rot, trusted 🟢 grün (Symbol + Spec-Label korrekt)")
    except Exception as e:
        record("F031", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
