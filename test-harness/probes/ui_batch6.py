#!/usr/bin/env python3
"""Verifikation UI-Batch 6 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F030  Voice-Mikrofon-Icon zeigt zwei States: grau (inaktiv) und grün (aktiv/aufnehmend). Gerendert
        wird das ECHTE Symbol+Farbe-Mapping des Titelleisten-Indikators (VoiceController.VoiceState
        .micSymbol/.micColor). Nachweis per Pixelfarbe: inaktiv grau, aktiv grün. Screenshots
        F030-grey.png / F030-green.png.
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


def capture(mode, dst: Path, min_w=200):
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


def classify(png: Path):
    im = Image.open(png).convert("RGB"); W, H = im.size; px = im.load()
    gray = green = icon = 0
    for y in range(0, H, 2):
        for x in range(0, W, 2):
            r, g, b = px[x, y]
            if r > 225 and g > 225 and b > 225:
                continue  # weißer Hintergrund
            icon += 1
            if g - r > 25 and g - b > 25:
                green += 1
            elif abs(r - g) < 28 and abs(g - b) < 28 and 40 < g < 215:
                gray += 1
    return {"icon_px": icon, "gray_px": gray, "green_px": green}


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    try:
        (EVID / "F030").mkdir(parents=True, exist_ok=True)
        idle = capture("mic-idle", EVID / "F030-grey.png", min_w=200)
        live = capture("mic-listening", EVID / "F030-green.png", min_w=200)
        assert idle and live, "Mic-Fenster fehlt"

        ci = classify(idle)
        cl = classify(live)
        # inaktiv: graues Icon (viel Grau, kaum Grün)
        assert ci["gray_px"] > 50 and ci["green_px"] < ci["gray_px"] * 0.3, f"inaktiv nicht grau: {ci}"
        # aktiv: grünes Icon (deutlich Grün)
        assert cl["green_px"] > 50 and cl["green_px"] > ci["green_px"] * 3, f"aktiv nicht grün: {cl}"
        record("F030", "pass", ev("F030", "mic-states.json", {"idle": ci, "listening": cl,
               "screens": ["test-harness/evidence/F030-grey.png", "test-harness/evidence/F030-green.png"]}),
               f"inaktiv grau ({ci['gray_px']}px grau / {ci['green_px']}px grün); aktiv grün ({cl['green_px']}px grün)")
    except Exception as e:
        record("F030", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
