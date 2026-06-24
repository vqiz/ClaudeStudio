#!/usr/bin/env python3
"""Verifikation UI-Batch 11 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F144  Kosten-USD-Counter läuft live mit und aktualisiert sich nach jeder Modell-Antwort. Der ECHTE
        CostTracker summiert die Kosten jeder Antwort; gerendert über den ECHTEN SessionCostFooter steigt
        der USD-Counter von 2 auf 6 Antworten messbar an. Per OCR der USD-Werte nachgewiesen.
        Screenshots F144-step1.png / F144-step2.png.
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


def read_usd(png: Path) -> float | None:
    im = Image.open(png).convert("RGB"); W, H = im.size
    crop = im.crop((int(W * 0.30), int(H * 0.28), int(W * 0.70), int(H * 0.52)))
    crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
    tmp = png.with_suffix(".ocr.png"); crop.save(tmp)
    txt = sh("tesseract", str(tmp), "stdout", "--psm", "6").stdout
    # USD-Wert extrahieren (z.B. $0.02 / 0,07)
    m = re.findall(r"(\d+[.,]\d+)", txt.replace(" ", ""))
    if not m:
        return None
    return max(float(x.replace(",", ".")) for x in m)


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    try:
        (EVID / "F144").mkdir(parents=True, exist_ok=True)
        s1 = capture("cost-step1", EVID / "F144-step1.png", min_w=300)
        s2 = capture("cost-step2", EVID / "F144-step2.png", min_w=300)
        assert s1 and s2, "Kosten-Fenster fehlt"
        v1 = read_usd(s1)
        v2 = read_usd(s2)
        assert v1 is not None and v2 is not None, f"USD-Wert nicht lesbar: step1={v1} step2={v2}"
        assert v2 > v1, f"Counter nicht gestiegen: step1={v1} step2={v2}"
        record("F144", "pass", ev("F144", "cost-counter.json",
               {"usd_after_2_responses": v1, "usd_after_6_responses": v2,
                "screens": ["test-harness/evidence/F144-step1.png", "test-harness/evidence/F144-step2.png"]}),
               f"Live-Kosten-Counter steigt mit jeder Antwort: ${v1:.3f} (2 Antworten) → ${v2:.3f} (6 Antworten)")
    except Exception as e:
        record("F144", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
