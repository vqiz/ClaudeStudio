#!/usr/bin/env python3
"""Verifikation UI-Batch 1 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F025  Statusfarben-Token: vier Badges (Akzent/Erfolg/Warnung/Fehler) rendern exakt
        #1A73E8 / #34A853 / #FBBC04 / #EA4335 (Pixel-Inspektion, Toleranz ±2).
  F022  cardShadow/dsCard(elevated:1) erzeugt einen weichen Schatten-Gradienten unter
        der Karte; die Vergleichskarte (elevated:false) nicht.
  F026  NavigationSplitView-Sidebar ist 260px breit (Bild-Inspektion der Trennlinie, ±4).

Methode: das Fenster wird per CGWindowList-ID gezielt aufgenommen (`screencapture -l`),
also fokus-unabhängig und ohne Accessibility-Klicks. F025/F022 nutzen die deterministische
Design-Galerie (`CLAUDESTUDIO_UITEST=gallery`); F026 die echte App-Shell.
PNGs werden nach sRGB konvertiert, um das Display-Profil zu neutralisieren.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402
from PIL import Image  # noqa: E402

ROOT = P.ROOT
EVID = ROOT / "test-harness" / "evidence"
LIB = ROOT / "test-harness" / "lib"
APP = ROOT / "app" / ".build" / "debug" / "ClaudeStudio"
CORE = ROOT / "core" / "target" / "debug" / "claudestudio-core"
WINDOWID = LIB / "windowid"
TMP = Path(os.environ.get("CLAUDE_JOB_DIR", "/tmp")) / "tmp"
TMP.mkdir(parents=True, exist_ok=True)
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def sh(*a, **kw):
    return subprocess.run(a, capture_output=True, text=True, **kw)


def kill_app():
    sh("pkill", "-f", "ClaudeStudio")
    sh("osascript", "-e", 'quit app "ClaudeStudio"')
    time.sleep(1)


def find_window(min_w):
    """CGWindowList-ID des größten On-Screen-ClaudeStudio-Fensters (layer 0)."""
    for _ in range(30):
        time.sleep(0.4)
        out = sh(str(WINDOWID), "ClaudeStudio").stdout
        best = None
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 4 and parts[3] == "0":
                w = int(parts[1])
                if w >= min_w and (best is None or w > best[1]):
                    best = (parts[0], w)
        if best:
            return best[0]
    return None


def capture_srgb(wid, dst: Path) -> Path:
    raw = dst.with_suffix(".raw.png")
    sh("screencapture", "-x", "-o", f"-l{wid}", str(raw))
    srgb = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"
    r = sh("sips", "--matchToWithIntent", srgb, "relative", str(raw), "--out", str(dst))
    if r.returncode != 0 or not dst.exists():
        dst.write_bytes(raw.read_bytes())
    return dst


def near(c, e, tol):
    return all(abs(c[k] - e[k]) <= tol for k in range(3))


# ---------- F025 + F022: Design-Galerie ----------
def verify_gallery():
    env = {**os.environ, "CLAUDESTUDIO_UITEST": "gallery"}
    proc = subprocess.Popen([str(APP)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wid = find_window(min_w=400)
        if not wid:
            raise RuntimeError("Galerie-Fenster nicht gefunden")
        time.sleep(1.0)
        (EVID / "F025").mkdir(parents=True, exist_ok=True)
        png = capture_srgb(wid, EVID / "F025" / "gallery.png")
        im = Image.open(png).convert("RGB")
        W, H = im.size
        px = im.load()

        # --- F025: vier Statusfarben messen (±2) ---
        # Das blaue accent-Badge ist eindeutig (kein blauer Fensterknopf), liefert
        # also robust den Galerie-Ursprung. Die anderen drei Badges werden an festen
        # Offsets gemessen — immun gegen die roten/gelben/grünen Ampel-Fensterknöpfe.
        exp = {"accent": (26, 115, 232), "success": (52, 168, 83),
               "warning": (251, 188, 4), "error": (234, 67, 53)}
        # accent per Median der exakt passenden Pixel lokalisieren (Toleranz 4).
        axs, ays = [], []
        for y in range(0, H, 2):
            for x in range(0, W, 2):
                if near(px[x, y], exp["accent"], 4):
                    axs.append(x); ays.append(y)
        if not axs:
            raise RuntimeError("accent-Badge nicht gefunden")
        axs.sort(); ays.sort()
        acx, acy = axs[len(axs) // 2], ays[len(ays) // 2]   # Median = Badge-Mitte
        # Galerie-Layout: Badge-Mitten content-x = [85,245,405,565], content-y = 59.
        Ox, Oy = acx - 85, acy - 59
        centers = {"accent": 85, "success": 245, "warning": 405, "error": 565}
        f025 = {}
        for nm, e in exp.items():
            cx, cy = Ox + centers[nm], Oy + 59
            rs, gs, bs = [], [], []
            for x in range(cx - 5, cx + 5):
                for y in range(cy - 5, cy + 5):
                    R, G, B = px[x, y]; rs.append(R); gs.append(G); bs.append(B)
            m = len(rs) // 2
            got = (sorted(rs)[m], sorted(gs)[m], sorted(bs)[m])
            f025[nm] = {"rgb": got, "expected": e, "sample_center": [cx, cy],
                        "max_delta": max(abs(got[k] - e[k]) for k in range(3))}
        ok025 = all(v["max_delta"] <= 2 for v in f025.values())

        # --- F022: Schattenband unter elevierter Karte (content y=300) ---
        def bandmean(x0, x1, y0, y1):
            s = n = 0
            for x in range(int(x0), int(x1)):
                for y in range(int(y0), int(y1)):
                    R, G, B = px[x, y]; s += (R + G + B) / 3; n += 1
            return s / n if n else 0
        yb = Oy + 300
        A = bandmean(Ox + 75, Ox + 265, yb + 1, yb + 10)    # eleviert (mit Schatten)
        B = bandmean(Ox + 495, Ox + 685, yb + 1, yb + 10)   # flach (ohne Schatten)
        base = bandmean(Ox + 310, Ox + 450, yb + 30, yb + 45)
        profile = {f"+{dy}px": round(bandmean(Ox + 75, Ox + 265, yb + dy, yb + dy + 1), 2)
                   for dy in range(0, 12, 2)}
        ok022 = (A < B - 1.0) and (A < base - 1.0)

        record("F025", "pass" if ok025 else "fail",
               ev("F025", "status-colors.json", {"png": str(png.relative_to(ROOT)), "badges": f025}),
               "Badges Akzent/Erfolg/Warnung/Fehler = #1A73E8/#34A853/#FBBC04/#EA4335 (±2)")
        record("F022", "pass" if ok022 else "fail",
               ev("F022", "card-shadow.json",
                  {"png": str(png.relative_to(ROOT)), "shadow_band_elevated": round(A, 2),
                   "flat_band": round(B, 2), "white_baseline": round(base, 2),
                   "gradient_profile": profile}),
               f"Schatten unter elevierter Karte ({A:.1f}) dunkler als flache ({B:.1f}) + Weiss ({base:.1f})")
    except Exception as e:  # noqa: BLE001
        record("F025", "fail", note=str(e)); record("F022", "fail", note=str(e))
    finally:
        proc.terminate(); kill_app()


# ---------- F026: echte App-Shell, Sidebar-Breite ----------
def verify_sidebar():
    sh("rm", "-f", str(Path.home() / ".claudestudio" / "core.sock"))
    for d in ("dev.claudestudio.app", "ClaudeStudio"):
        sh("defaults", "delete", d)  # persistierte Spaltenbreite zurücksetzen
    core = subprocess.Popen([str(CORE)], env={**os.environ, "CLAUDESTUDIO_LIBRARY_DIR": str(ROOT)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    env = {k: v for k, v in os.environ.items() if k != "CLAUDESTUDIO_UITEST"}
    env["CLAUDESTUDIO_LIBRARY_DIR"] = str(ROOT)
    proc = subprocess.Popen([str(APP)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wid = find_window(min_w=800)
        if not wid:
            raise RuntimeError("Shell-Fenster nicht gefunden")
        time.sleep(2.0)
        (EVID / "F026").mkdir(parents=True, exist_ok=True)
        png = capture_srgb(wid, EVID / "F026" / "shell.png")
        im = Image.open(png).convert("RGB")
        W, H = im.size
        px = im.load()
        # Trennlinie Sidebar/Content: x mit den meisten ganzhohen Farbkanten (Schwelle 5)
        from collections import Counter
        votes = Counter()
        for y in range(int(H * 0.2), int(H * 0.8), 2):
            for x in range(150, min(360, W)):
                if sum(abs(px[x, y][i] - px[x - 1, y][i]) for i in range(3)) > 5:
                    votes[x] += 1
        cand = [(x, c) for x, c in votes.most_common(8) if c > 50]
        divx = max(cand, key=lambda t: t[1])[0] if cand else None
        ok = divx is not None and abs(divx - 260) <= 4
        record("F026", "pass" if ok else "fail",
               ev("F026", "sidebar-width.json",
                  {"png": str(png.relative_to(ROOT)), "divider_x_pt": divx,
                   "candidates": sorted(cand), "spec": "260 ±4"}),
               f"Sidebar-Trennlinie bei {divx}pt (Spec 260 ±4)")
    except Exception as e:  # noqa: BLE001
        record("F026", "fail", note=str(e))
    finally:
        proc.terminate(); core.terminate(); kill_app()


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": f"App nicht gebaut: {APP}"}}}))
        return
    kill_app()
    verify_gallery()
    verify_sidebar()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
