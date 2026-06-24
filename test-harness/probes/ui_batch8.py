#!/usr/bin/env python3
"""Verifikation UI-Batch 8 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F235  Status-Icon-Farben spiegeln den Voice-Zustand wider: grau idle, grün hört, orange denkt,
        blau spricht — gerendert über das ECHTE VoiceController.VoiceState.micColor-Mapping (dieselbe
        Quelle wie der Titelleisten-Indikator). Alle vier Farben per Pixel nachgewiesen. Screenshots
        F235-<state>.png.
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


def icon_hue(png: Path) -> dict:
    """Dominante Icon-Farbe klassifizieren (grau/grün/orange/blau) über die nicht-weißen Pixel."""
    im = Image.open(png).convert("RGB"); W, H = im.size; px = im.load()
    cnt = {"gray": 0, "green": 0, "orange": 0, "blue": 0, "icon": 0}
    for y in range(0, H, 2):
        for x in range(0, W, 2):
            r, g, b = px[x, y]
            if r > 225 and g > 225 and b > 225:
                continue
            cnt["icon"] += 1
            if g - r > 25 and g - b > 25:
                cnt["green"] += 1
            elif b - r > 25 and b - g > 25:
                cnt["blue"] += 1
            elif r - b > 40 and g - b > 10 and r - g > 15:   # orange: R hoch, G mittel, B niedrig
                cnt["orange"] += 1
            elif abs(r - g) < 28 and abs(g - b) < 28 and 40 < g < 215:
                cnt["gray"] += 1
    return cnt


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    states = {"idle": ("mic-idle", "gray"), "listening": ("mic-listening", "green"),
              "thinking": ("mic-thinking", "orange"), "speaking": ("mic-speaking", "blue")}
    try:
        (EVID / "F235").mkdir(parents=True, exist_ok=True)
        measured = {}
        for st, (mode, expected) in states.items():
            png = capture(mode, EVID / f"F235-{st}.png", min_w=200)
            assert png, f"Fenster fehlt für {st}"
            cnt = icon_hue(png)
            measured[st] = cnt
            dom = max(("gray", "green", "orange", "blue"), key=lambda k: cnt[k])
            assert dom == expected and cnt[expected] > 40, \
                f"{st}: erwartet {expected}, dominant {dom} ({cnt})"
        record("F235", "pass", ev("F235", "voice-status-colors.json",
               {"measured": measured,
                "screens": [f"test-harness/evidence/F235-{s}.png" for s in states]}),
               "alle vier Voice-States farblich korrekt: idle grau, listening grün, thinking orange, speaking blau")
    except Exception as e:
        record("F235", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
