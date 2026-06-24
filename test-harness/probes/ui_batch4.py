#!/usr/bin/env python3
"""Verifikation UI-Batch 4 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F018  Sortierbare Sessions-Tabelle: echte SwiftUI `Table` mit KeyPathComparator auf Kosten.
        Aufsteigend steht 0,10 in Zeile 1 (oben), absteigend 0,90 in Zeile 1 — per OCR-Bounding-
        Box (y-Position) nachgewiesen. Screenshots F018-asc.png / F018-desc.png.
  F019  Drei Dichte-Stufen ändern Zeilenhöhe/Padding: im Kompakt-Modus ist der Zeilen-Pitch
        messbar kleiner als im Geräumig-Modus (10 Zeilen, OCR-Bounding-Boxen). Screenshots
        F019-kompakt.png / F019-geraeumig.png.
"""
from __future__ import annotations
import csv, io, json, os, re, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

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


def tsv(png: Path) -> list[dict]:
    out = sh("tesseract", str(png), "stdout", "--psm", "6", "tsv").stdout
    rows = []
    for r in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE):
        txt = (r.get("text") or "").strip()
        if not txt:
            continue
        try:
            rows.append({"text": txt, "left": int(r["left"]), "top": int(r["top"]),
                         "w": int(r["width"]), "h": int(r["height"])})
        except (ValueError, KeyError):
            pass
    return rows


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()

    # ---- F018: sortierbare Tabelle, Reihenfolge per Bounding-Box ----
    try:
        (EVID / "F018").mkdir(parents=True, exist_ok=True)
        asc = capture("table-asc", EVID / "F018" / "asc.png", min_w=400)
        desc = capture("table-desc", EVID / "F018" / "desc.png", min_w=400)
        assert asc and desc, "Tabellen-Fenster fehlt"
        (EVID / "F018-asc.png").write_bytes(asc.read_bytes())
        (EVID / "F018-desc.png").write_bytes(desc.read_bytes())

        def cost_tops(png):
            # y-Position der Kosten 0,10 ('10') und 0,90 ('90') — eindeutig (keine Dates/Dauern).
            y10 = y90 = None
            for t in tsv(png):
                flat = t["text"].replace(" ", "")
                if "90" in flat and y90 is None:
                    y90 = t["top"]
                if re.search(r"(^|\D)10(\D|$)", flat) and "910" not in flat and y10 is None:
                    y10 = t["top"]
            return y10, y90

        a10, a90 = cost_tops(asc)
        d10, d90 = cost_tops(desc)
        assert None not in (a10, a90, d10, d90), f"Kosten nicht erkannt: asc=({a10},{a90}) desc=({d10},{d90})"
        assert a10 < a90, f"aufsteigend: 0,10 nicht oben (y10={a10} y90={a90})"
        assert d90 < d10, f"absteigend: 0,90 nicht oben (y90={d90} y10={d10})"
        record("F018", "pass", ev("F018", "sort-order.json",
               {"asc_y": {"0,10": a10, "0,90": a90}, "desc_y": {"0,90": d90, "0,10": d10},
                "screens": ["test-harness/evidence/F018-asc.png", "test-harness/evidence/F018-desc.png"]}),
               f"aufsteigend 0,10 oben (y={a10}<{a90}); absteigend 0,90 oben (y={d90}<{d10})")
    except Exception as e:
        record("F018", "fail", note=str(e))

    # ---- F019: Dichte-Stufen, Zeilen-Pitch per Bounding-Box ----
    try:
        (EVID / "F019").mkdir(parents=True, exist_ok=True)
        komp = capture("density-kompakt", EVID / "F019" / "kompakt.png", min_w=300)
        gera = capture("density-geraeumig", EVID / "F019" / "geraeumig.png", min_w=300)
        assert komp and gera, "Dichte-Fenster fehlt"
        (EVID / "F019-kompakt.png").write_bytes(komp.read_bytes())
        (EVID / "F019-geraeumig.png").write_bytes(gera.read_bytes())

        def row_pitch(png):
            tops = sorted(t["top"] for t in tsv(png) if t["text"].lower().startswith("zeile"))
            if len(tops) < 4:
                return None, len(tops)
            return (tops[-1] - tops[0]) / (len(tops) - 1), len(tops)

        pk, nk = row_pitch(komp)
        pg, ng = row_pitch(gera)
        assert pk and pg, f"zu wenige Zeilen erkannt: kompakt={nk}, geraeumig={ng}"
        assert pk < pg * 0.85, f"Kompakt-Pitch nicht kleiner: kompakt={pk:.1f} geraeumig={pg:.1f}"
        record("F019", "pass", ev("F019", "density-pitch.json",
               {"kompakt": {"row_pitch_px": round(pk, 1), "rows": nk},
                "geraeumig": {"row_pitch_px": round(pg, 1), "rows": ng},
                "screens": ["test-harness/evidence/F019-kompakt.png", "test-harness/evidence/F019-geraeumig.png"]}),
               f"Kompakt-Zeilen-Pitch {pk:.1f}px < Geräumig {pg:.1f}px ({nk}/{ng} Zeilen)")
    except Exception as e:
        record("F019", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
