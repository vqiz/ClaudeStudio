#!/usr/bin/env python3
"""Verifikation UI-Batch 20 (echte SwiftUI-App, fenster-gezielter Screenshot, ECHTE UserDefaults-Persistenz):

  F023  Dashboard-Karten sind kollabierbar + per Drag&Drop umsortierbar; die Reihenfolge bleibt nach
        Neustart erhalten. Über das ECHTE DashboardCardLayout.save() (UserDefaults) wird die Anordnung
        B,A,C mit kollabierter Karte C gesetzt (= Drag&Drop+Kollaps-Effekt) und gerendert; nach App-
        NEUSTART (ohne erneutes Setzen) ist die Anordnung B,A,C + C kollabiert weiterhin erhalten.
        Reihenfolge per OCR-Bounding-Box, Persistenz über zwei App-Starts. Screenshots
        F023-reorder.png / F023-persist.png.
"""
from __future__ import annotations
import csv, io, json, os, subprocess, sys, time
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


def capture(dst: Path, cardlayout: str | None, min_w=300):
    env = {**os.environ, "CLAUDESTUDIO_UITEST": "cards"}
    if cardlayout is not None:
        env["CLAUDESTUDIO_CARDLAYOUT"] = cardlayout
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


def tsv_tokens(png: Path):
    out = sh("tesseract", str(png), "stdout", "--psm", "6", "tsv").stdout
    toks = []
    for r in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE):
        t = (r.get("text") or "").strip().lower()
        if not t:
            continue
        try:
            toks.append((t, int(r["top"])))
        except (ValueError, KeyError):
            pass
    return toks


def analyse(png: Path):
    toks = tsv_tokens(png)
    text = " ".join(t for t, _ in toks)
    # y-Position der eindeutigen Karten-Titel: Kosten=B, Sessions=A, Agenten=C
    def ytop(word):
        ys = [y for t, y in toks if t == word]
        return min(ys) if ys else None
    yB, yA, yC = ytop("kosten"), ytop("sessions"), ytop("agenten")
    flat = text.replace(" ", "")
    return {"yB": yB, "yA": yA, "yC": yC,
            "bodyA": "kartea" in flat, "bodyB": "karteb" in flat, "bodyC": "kartec" in flat,
            "text": text[:200]}


def check(a):
    assert None not in (a["yB"], a["yA"], a["yC"]), f"Karten-Titel nicht erkannt: {a}"
    assert a["yB"] < a["yA"] < a["yC"], f"Reihenfolge nicht B,A,C: B={a['yB']} A={a['yA']} C={a['yC']}"
    # A + B aufgeklappt (Body sichtbar), C kollabiert (kein Body)
    assert a["bodyA"] and a["bodyB"] and not a["bodyC"], f"Kollaps-Zustand falsch: {a}"


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    try:
        (EVID / "F023").mkdir(parents=True, exist_ok=True)
        # 1) Drag&Drop + Kollaps setzen (Reihenfolge B,A,C, Karte C kollabiert) -> persistiert
        reorder = capture(EVID / "F023-reorder.png", "B,A,C;C")
        # 2) App-NEUSTART ohne erneutes Setzen -> persistierte Anordnung wird geladen
        persist = capture(EVID / "F023-persist.png", None)
        assert reorder and persist, "Karten-Fenster fehlt"
        a1 = analyse(reorder); a2 = analyse(persist)
        check(a1)   # nach dem Umsortieren
        check(a2)   # nach Neustart weiterhin so (persistiert)
        record("F023", "pass", ev("F023", "card-reorder-persist.json",
               {"after_reorder": a1, "after_restart": a2,
                "screens": ["test-harness/evidence/F023-reorder.png", "test-harness/evidence/F023-persist.png"]}),
               "Karten umsortiert auf B,A,C (C kollabiert); nach Neustart Reihenfolge + Kollaps-Zustand erhalten")
    except Exception as e:
        record("F023", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
