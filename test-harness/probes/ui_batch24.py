#!/usr/bin/env python3
"""Verifikation UI-Batch 24 (echter Core + ECHTES Whisper.cpp + echte SwiftUI-App, kein Mock):

  F230  Gesprochener Befehl 'Ändere die Hintergrundfarbe auf blau' wird ausgeführt und visuell
        angewendet. Kette: macOS `say` (deutsche Stimme) → Core voice.run_command (Whisper.cpp-STT +
        Intent-Parsing → set_background_color=blue) → die App wendet die Farbe sichtbar als
        Fensterhintergrund an. Per Pixelfarbe nachgewiesen. Screenshot F230-applied.png.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402
from PIL import Image  # noqa: E402

ROOT = P.ROOT
WHISPER = "/opt/homebrew/bin/whisper-cli"
MODEL = ROOT / "test-harness" / "lib" / "whisper-models" / "ggml-base.bin"
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


def capture_bg(color: str, dst: Path):
    env = {**os.environ, "CLAUDESTUDIO_UITEST": "bgcolor", "CLAUDESTUDIO_BGCOLOR": color}
    proc = subprocess.Popen([str(APP)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wid = find_window(300)
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


def blue_fraction(png: Path) -> float:
    im = Image.open(png).convert("RGB"); W, H = im.size; px = im.load()
    blue = tot = 0
    for y in range(int(H * 0.2), int(H * 0.9), 3):
        for x in range(int(W * 0.1), int(W * 0.9), 3):
            r, g, b = px[x, y]; tot += 1
            if b > 150 and b > r + 40 and b > g + 30:
                blue += 1
    return blue / max(tot, 1)


def main():
    if not APP.exists() or not MODEL.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App/Modell fehlt"}}})); return
    kill_app()
    # 1) Deutschen Sprachbefehl als Audio erzeugen
    tmp = Path(tempfile.mkdtemp(prefix="cs-f230-"))
    aiff, wav = tmp / "v.aiff", tmp / "v.wav"
    cmd = "Ändere die Hintergrundfarbe auf blau"
    if sh("say", "-v", "Anna", cmd, "-o", str(aiff)).returncode != 0:
        sh("say", cmd, "-o", str(aiff))
    sh("afconvert", str(aiff), "-o", str(wav), "-f", "WAVE", "-d", "LEI16@16000")

    try:
        (EVID / "F230").mkdir(parents=True, exist_ok=True)
        # 2) Core: STT + Intent
        with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b80.log")) as ctx:
            c = P.Client(ctx["sock"], timeout=120)
            r = c.request("voice.run_command",
                          {"audio": str(wav), "model": str(MODEL), "whisper_bin": WHISPER, "language": "de"})
            c.close()
        assert r["ok"] and r["action"] == "set_background_color", f"Intent falsch: {r}"
        assert r["color"] == "blue", f"Farbe nicht blau: {r}"
        assert "hintergrund" in r["transcript"].lower() and "blau" in r["transcript"].lower(), \
            f"Transkript unerwartet: {r['transcript']!r}"

        # 3) App wendet die erkannte Farbe sichtbar an
        png = capture_bg(r["color"], EVID / "F230-applied.png")
        assert png, "BG-Fenster fehlt"
        frac = blue_fraction(png)
        assert frac > 0.6, f"Hintergrund nicht überwiegend blau ({frac:.0%})"

        record("F230", "pass", ev("F230", "voice-bgcolor.json",
               {"transcript": r["transcript"], "action": r["action"], "color": r["color"],
                "blue_fraction": round(frac, 2), "screen": "test-harness/evidence/F230-applied.png"}),
               f"Sprachbefehl '{r['transcript'].strip()}' → set_background_color=blue, sichtbar angewendet ({frac:.0%} blau)")
    except Exception as e:
        record("F230", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
