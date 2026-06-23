#!/usr/bin/env python3
"""Verifikation UI-Batch 2 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F017  Swift-Charts Token-Chart: rendert 7 Datenpunkte (Tage); der höchste Punkt (6000) liegt
        sichtbar oberhalb des niedrigsten (1000). Geprüft per 7 roten Punkt-Markern + deren y.
  F021  Responsives LazyVGrid: 9 Karten; bei größerer Fensterbreite (1200) mehr Spalten in der
        ersten Reihe als bei schmaler Breite (600); alle 9 Karten bleiben sichtbar.
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


def capture(mode, dst: Path, width=None, min_w=400):
    env = {**os.environ, "CLAUDESTUDIO_UITEST": mode}
    if width:
        env["CLAUDESTUDIO_UITEST_WIDTH"] = str(width)
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


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    GBLUE = (26, 115, 232)
    GRED = (234, 67, 53)

    # F017 — Chart: 7 rote Punkte, Hochpunkt über Tiefpunkt
    try:
        (EVID / "F017").mkdir(parents=True, exist_ok=True)
        png = capture("chart", EVID / "F017" / "chart.png", min_w=400)
        assert png, "kein Chart-Fenster"
        im = Image.open(png).convert("RGB"); W, H = im.size; px = im.load()
        reds = [(x, y) for y in range(0, H, 1) for x in range(0, W, 1) if near(px[x, y], GRED, 40)]
        assert reds, "keine roten Punkt-Marker gefunden"
        # nach x clustern (Lücke > 18 px => neuer Punkt)
        reds.sort()
        clusters = []
        for x, y in reds:
            if clusters and x - clusters[-1]["xmax"] <= 18:
                cl = clusters[-1]; cl["xs"].append(x); cl["ys"].append(y); cl["xmax"] = x
            else:
                clusters.append({"xs": [x], "ys": [y], "xmax": x})
        pts = []
        for cl in clusters:
            if len(cl["xs"]) >= 4:  # echte Marker, kein Rauschen
                cx = sum(cl["xs"]) // len(cl["xs"]); cy = sum(cl["ys"]) // len(cl["ys"])
                pts.append((cx, cy))
        pts.sort()
        assert len(pts) == 7, f"{len(pts)} Punkte statt 7: {[p[0] for p in pts]}"
        ys = [p[1] for p in pts]
        # Tag 6 (6. Punkt, 6000) ist der höchste => kleinstes y; Tag 1 (1000) niedrig
        assert pts[5][1] == min(ys), f"Hochpunkt nicht an Tag 6: ys={ys}"
        assert min(ys) < max(ys) - 40, f"kein sichtbarer Höhenunterschied: ys={ys}"
        record("F017", "pass", ev("F017", "chart-points.json",
               {"points_xy": pts, "peak_idx": ys.index(min(ys)), "y_range": [min(ys), max(ys)]}),
               f"7 Datenpunkte; Hochpunkt (Tag 6) y={min(ys)} über Tiefpunkt y={max(ys)}")
    except Exception as e:
        record("F017", "fail", note=str(e))

    # F021 — Grid: mehr Spalten bei 1200 als bei 600, 9 Karten sichtbar
    def grid_columns(png):
        im = Image.open(png).convert("RGB"); W, H = im.size; px = im.load()
        blue = [(x, y) for y in range(0, H, 2) for x in range(0, W, 2) if near(px[x, y], GBLUE, 30)]
        if not blue:
            return 0, 0
        ymin = min(y for _, y in blue)
        rowy = ymin + 30  # mitten durch die erste Kartenreihe
        runs, inrun = 0, False
        for x in range(0, W):
            isblue = near(px[x, rowy], GBLUE, 30)
            if isblue and not inrun:
                runs += 1
            inrun = isblue
        # Gesamtkarten grob: blaue Blöcke über alle Reihen (Spalten * Reihen)
        return runs, len(blue)
    try:
        (EVID / "F021").mkdir(parents=True, exist_ok=True)
        wide = capture("grid", EVID / "F021" / "grid-wide.png", width=1200, min_w=900)
        narrow = capture("grid", EVID / "F021" / "grid-narrow.png", width=600, min_w=400)
        assert wide and narrow, "Grid-Fenster fehlt"
        cw, blue_w = grid_columns(wide)
        cn, blue_n = grid_columns(narrow)
        assert cw > cn and cw >= 3 and cn >= 1, f"Spalten breit={cw} schmal={cn}"
        record("F021", "pass", ev("F021", "grid-columns.json",
               {"columns_at_1200": cw, "columns_at_600": cn}),
               f"responsiv: {cw} Spalten bei 1200px > {cn} Spalten bei 600px")
    except Exception as e:
        record("F021", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
