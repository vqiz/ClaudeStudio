#!/usr/bin/env python3
"""Verifikation UI-Batch 13 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F148  Findings und Warnungen werden inline im Output dargestellt, inklusive Zeilennummer. Die ECHTEN
        TranscriptRow-Karten rendern `.finding`-Events als hervorgehobene Inline-Blöcke mit Schweregrad,
        Nachricht und Datei:Zeilennummer (z. B. ein Security-Finding in src/db.js:42). Per OCR
        nachgewiesen. Screenshot F148-findings.png.
"""
from __future__ import annotations
import json, os, re, subprocess, sys, time
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


def ocr(png: Path) -> str:
    im = Image.open(png).convert("RGB"); W, H = im.size
    crop = im.crop((int(W * 0.28), int(H * 0.16), int(W * 0.74), int(H * 0.84)))
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    tmp = png.with_suffix(".ocr.png"); crop.save(tmp)
    return sh("tesseract", str(tmp), "stdout", "--psm", "6").stdout.lower()


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    try:
        (EVID / "F148").mkdir(parents=True, exist_ok=True)
        png = capture("findings", EVID / "F148-findings.png", min_w=350)
        assert png, "Findings-Fenster fehlt"
        t = ocr(png)
        flat = t.replace(" ", "")
        # Schweregrade sichtbar
        assert "high" in t, f"Schweregrad HIGH fehlt: {t[:200]!r}"
        assert "medium" in t, f"Schweregrad MEDIUM fehlt: {t[:200]!r}"
        # Dateibezug + Zeilennummer (inline)
        assert "db.js" in flat and re.search(r"db\.?js[:\s]*42", flat), f"db.js:42 fehlt: {t[:240]!r}"
        assert "auth.js" in flat and re.search(r"auth\.?js[:\s]*88", flat), f"auth.js:88 fehlt: {t[:240]!r}"
        # Nachricht (Security-Bezug)
        assert "sql" in t or "injection" in t, f"Finding-Nachricht fehlt: {t[:240]!r}"
        record("F148", "pass", ev("F148", "inline-findings.json",
               {"ocr": t[:300], "screen": "test-harness/evidence/F148-findings.png"}),
               "Findings inline: HIGH (db.js:42, SQL-Injection) + MEDIUM (auth.js:88) mit Zeilennummern")
    except Exception as e:
        record("F148", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
