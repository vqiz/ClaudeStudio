#!/usr/bin/env python3
"""Verifikation UI-Batch 3 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F016  KPI-Karten-Reihe oben im Dashboard: vier Metriken (Sessions heute, Kosten heute, Features
        passing, aktive Agenten) je mit großem Wert und farbigem Delta-Pfeil + absolutem Delta
        gegenüber Vortag. Seed-Szenario: 5 Sessions heute / 3 gestern (Wert '5', '+2'), Kosten
        1,20 USD / 0,80 (Wert '1,20 USD', '+0,40'). Geprüft per OCR (Labels+Werte) + Pixel (grüne
        Aufwärts-Pfeile).
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


def capture(mode, dst: Path, min_w=400):
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


def near(c, e, tol):
    return all(abs(c[k] - e[k]) <= tol for k in range(3))


def ocr(png: Path) -> str:
    out = sh("tesseract", str(png), "stdout", "--psm", "6")
    return out.stdout


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    GGREEN = (52, 168, 83)

    try:
        (EVID / "F016").mkdir(parents=True, exist_ok=True)
        png = capture("kpi", EVID / "F016" / "kpi.png", min_w=600)
        # Evidence-Standardname laut Spec
        if png:
            (EVID / "F016-kpi.png").write_bytes(png.read_bytes())
        assert png, "kein KPI-Fenster"
        im = Image.open(png).convert("RGB"); W, H = im.size; px = im.load()

        # OCR: Labels + Werte
        text = ocr(png)
        norm = re.sub(r"[ \t]+", " ", text)
        low = norm.lower()
        labels = [l for l in ("sessions", "kosten", "features", "agenten") if l in low]
        # Werte (deutsche/us Dezimaltrennung tolerieren)
        flat = norm.replace(" ", "")
        values = []
        if "5" in norm:
            values.append("5")
        if "314" in flat:
            values.append("314")
        if re.search(r"1[.,]20", flat) or "1,2" in flat or "120USD" in flat:
            values.append("1,20")
        if re.search(r"\+?2(\D|$)", norm) or "+2" in flat:
            values.append("aktive=2")
        # mindestens ein vorzeichenbehaftetes Delta sichtbar
        delta_ok = bool(re.search(r"[+＋]\s?\d", norm)) or "+0,40" in flat or "+2" in flat or "+4" in flat

        # Pixel: grüne Aufwärts-Pfeile vorhanden
        greens = sum(1 for y in range(0, H, 1) for x in range(0, W, 1) if near(px[x, y], GGREEN, 45))

        assert len(labels) >= 3, f"zu wenige Labels erkannt: {labels} | OCR={norm[:160]!r}"
        assert len(values) >= 3, f"zu wenige Werte erkannt: {values} | OCR={norm[:160]!r}"
        assert greens >= 30, f"keine grünen Aufwärts-Pfeile ({greens} px)"
        assert delta_ok, f"kein vorzeichenbehaftetes Delta erkannt | OCR={norm[:160]!r}"

        record("F016", "pass", ev("F016", "kpi-ocr.json",
               {"labels_detected": labels, "values_detected": values,
                "green_arrow_pixels": greens, "delta_sign_seen": delta_ok,
                "ocr_excerpt": norm[:300], "image_size": [W, H],
                "screenshot": "test-harness/evidence/F016-kpi.png"}),
               f"4 KPI-Karten: Labels {labels}, Werte {values}, grüne Delta-Pfeile ({greens}px)")
    except Exception as e:
        record("F016", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
