#!/usr/bin/env python3
"""Verifikation UI-Batch 17 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F143  Approval-Flow je Trust-Modus: über die ECHTE TrustMode.requiresApproval(destructive:)-Logik wird
        eine destruktive Operation (rm -rf) im Modus Guarded zur Bestätigung vorgelegt (Approve/Deny-Prompt),
        im Modus Unleashed ohne Rückfrage ausgeführt. Per OCR beider Modi nachgewiesen.
        Screenshots F143-ask.png / F143-auto.png.
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


def ocr(png: Path) -> str:
    im = Image.open(png).convert("RGB"); W, H = im.size
    crop = im.crop((int(W * 0.28), int(H * 0.16), int(W * 0.74), int(H * 0.80)))
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    tmp = png.with_suffix(".ocr.png"); crop.save(tmp)
    return sh("tesseract", str(tmp), "stdout", "--psm", "6").stdout.lower()


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    try:
        (EVID / "F143").mkdir(parents=True, exist_ok=True)
        ask = capture("approval-ask", EVID / "F143-ask.png", min_w=300)
        auto = capture("approval-auto", EVID / "F143-auto.png", min_w=300)
        assert ask and auto, "Approval-Fenster fehlt"
        t_ask = ocr(ask)
        t_auto = ocr(auto)
        # Guarded: Bestätigungs-Prompt mit Approve/Deny
        assert "guarded" in t_ask, f"ask-Modus nicht Guarded: {t_ask[:160]!r}"
        assert "approval" in t_ask and "approve" in t_ask and "deny" in t_ask, \
            f"kein Bestätigungs-Prompt im Guarded-Modus: {t_ask[:220]!r}"
        # Unleashed: ohne Rückfrage ausgeführt, KEIN Deny-Button
        assert "unleashed" in t_auto, f"auto-Modus nicht Unleashed: {t_auto[:160]!r}"
        assert "auto" in t_auto and "deny" not in t_auto, \
            f"Unleashed zeigt Bestätigungs-Prompt statt Auto-Run: {t_auto[:220]!r}"
        record("F143", "pass", ev("F143", "approval-flow.json",
               {"guarded_ocr": t_ask[:220], "unleashed_ocr": t_auto[:220],
                "screens": ["test-harness/evidence/F143-ask.png", "test-harness/evidence/F143-auto.png"]}),
               "Approval-Flow: Guarded legt rm -rf zur Bestätigung vor (Approve/Deny); Unleashed läuft ohne Rückfrage")
    except Exception as e:
        record("F143", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
