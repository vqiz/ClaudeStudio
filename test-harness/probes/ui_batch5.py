#!/usr/bin/env python3
"""Verifikation UI-Batch 5 (echte SwiftUI-App, fenster-gezielter Screenshot, ECHTE UserDefaults-Persistenz):

  F024  Light/Dark-Mode + Persistenz über Neustart: über das ECHTE AppTheme.save() (UserDefaults)
        gesetzt + per .themedChrome() angewendet. Nachweis per Fensterhintergrund-Helligkeit:
        Light-Start hell, nach 'Umschalten' auf Dark dunkel (F024-dark.png), nach App-NEUSTART
        (ohne erneutes Setzen) weiterhin dunkel (F024-after-restart.png) — die Auswahl wurde
        persistiert.
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


def capture(dst: Path, theme_env: str | None, min_w=400):
    env = {**os.environ, "CLAUDESTUDIO_UITEST": "theme"}
    if theme_env is not None:
        env["CLAUDESTUDIO_THEME"] = theme_env
    proc = subprocess.Popen([str(APP)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wid = find_window(min_w)
        if not wid:
            return None
        time.sleep(1.2)
        raw = dst.with_suffix(".raw.png")
        sh("screencapture", "-x", "-o", f"-l{wid}", str(raw))
        srgb = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"
        r = sh("sips", "--matchToWithIntent", srgb, "relative", str(raw), "--out", str(dst))
        if r.returncode != 0 or not dst.exists():
            dst.write_bytes(raw.read_bytes())
        return dst
    finally:
        proc.terminate(); kill_app()


def content_luminance(png: Path) -> float:
    im = Image.open(png).convert("RGB"); W, H = im.size; px = im.load()
    # Zentrale Inhaltsregion (Titelleiste oben + Ränder ausklammern).
    x0, x1 = int(W * 0.20), int(W * 0.80)
    y0, y1 = int(H * 0.30), int(H * 0.85)
    tot = n = 0
    for y in range(y0, y1, 2):
        for x in range(x0, x1, 2):
            r, g, b = px[x, y]
            tot += (r + g + b) / 3.0; n += 1
    return tot / max(n, 1)


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    try:
        (EVID / "F024").mkdir(parents=True, exist_ok=True)
        # 1) bekannter Light-Ausgangszustand
        light = capture(EVID / "F024" / "light.png", "light")
        # 2) auf Dark 'umschalten' (echtes save() -> UserDefaults) + anwenden
        dark = capture(EVID / "F024-dark.png", "dark")
        # 3) App-NEUSTART OHNE erneutes Setzen -> persistierte Auswahl wird geladen
        restart = capture(EVID / "F024-after-restart.png", None)
        assert light and dark and restart, "Theme-Fenster fehlt"

        ll = content_luminance(light)
        dl = content_luminance(dark)
        rl = content_luminance(restart)

        assert ll > 150, f"Light nicht hell genug (lum={ll:.0f})"
        assert dl < 120, f"Dark nicht dunkel genug (lum={dl:.0f})"
        assert ll - dl > 60, f"zu geringer Light/Dark-Kontrast ({ll:.0f} vs {dl:.0f})"
        assert rl < 120, f"nach Neustart NICHT dunkel — Persistenz fehlgeschlagen (lum={rl:.0f})"
        assert abs(rl - dl) < 50, f"Neustart-Helligkeit weicht von Dark ab ({rl:.0f} vs {dl:.0f})"

        record("F024", "pass", ev("F024", "theme-persist.json",
               {"luminance": {"light": round(ll, 1), "dark": round(dl, 1), "after_restart": round(rl, 1)},
                "screens": ["test-harness/evidence/F024-dark.png", "test-harness/evidence/F024-after-restart.png"]}),
               f"Light lum={ll:.0f} → Dark lum={dl:.0f}; nach Neustart weiterhin dunkel lum={rl:.0f} (persistiert)")
    except Exception as e:
        record("F024", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
