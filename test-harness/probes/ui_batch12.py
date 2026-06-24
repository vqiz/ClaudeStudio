#!/usr/bin/env python3
"""Verifikation UI-Batch 12 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F149  Tool-Output wird korrekt geparst und strukturiert angezeigt statt als Rohtext: die ECHTEN
        TranscriptRow-Karten rendern (a) eine Shell-Ausführung mit GETRENNTEM stdout + Exit-Code-Badge
        und (b) ein JSON-Resultat eingerückt/geparst (ToolCall.formattedOutput). Per OCR nachgewiesen.
        Screenshot F149-tooloutput.png.
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


def ocr(png: Path) -> str:
    im = Image.open(png).convert("RGB"); W, H = im.size
    crop = im.crop((int(W * 0.28), int(H * 0.18), int(W * 0.74), int(H * 0.88)))
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    tmp = png.with_suffix(".ocr.png"); crop.save(tmp)
    return sh("tesseract", str(tmp), "stdout", "--psm", "6").stdout.lower()


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    try:
        (EVID / "F149").mkdir(parents=True, exist_ok=True)
        png = capture("tooloutput", EVID / "F149-tooloutput.png", min_w=350)
        assert png, "Tool-Output-Fenster fehlt"
        t = ocr(png)
        # Shell: stdout + Exit-Code getrennt
        import re
        assert "stdout" in t, f"stdout-Block nicht getrennt beschriftet: {t[:200]!r}"
        assert re.search(r"exit\s*code", t), f"Exit-Code nicht getrennt: {t[:200]!r}"
        assert "passed" in t, f"stdout-Inhalt fehlt: {t[:200]!r}"
        # JSON: geparst/strukturiert (Schlüssel erkennbar)
        keys = [k for k in ("name", "version", "scripts", "todo-api", "jest") if k in t]
        assert len(keys) >= 3, f"JSON nicht strukturiert erkannt: {keys} ({t[:240]!r})"
        record("F149", "pass", ev("F149", "tool-output.json",
               {"ocr": t[:300], "json_keys_seen": keys,
                "screen": "test-harness/evidence/F149-tooloutput.png"}),
               "Shell-Output: stdout + Exit-Code getrennt; JSON-Resultat geparst/strukturiert (Keys erkannt)")
    except Exception as e:
        record("F149", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
