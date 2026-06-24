#!/usr/bin/env python3
"""Verifikation UI-Batch 9 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F236  Voice-Log speichert alle Interaktionen als durchsuchbaren Text: der ECHTE VoiceLog.search()
        filtert die persistierten Einträge per Volltext. Ohne Filter erscheinen alle vier
        Interaktionen; die Suche 'security' liefert nur die Security-Review-Interaktion. Per OCR
        nachgewiesen. Screenshots F236-all.png / F236-search.png.
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


def capture(mode, dst: Path, extra_env=None, min_w=400):
    env = {**os.environ, "CLAUDESTUDIO_UITEST": mode}
    if extra_env:
        env.update(extra_env)
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
    crop = im.crop((int(W * 0.30), int(H * 0.30), int(W * 0.74), int(H * 0.80)))
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    tmp = png.with_suffix(".ocr.png"); crop.save(tmp)
    return sh("tesseract", str(tmp), "stdout", "--psm", "6").stdout.lower()


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    try:
        (EVID / "F236").mkdir(parents=True, exist_ok=True)
        allp = capture("voicelog-all", EVID / "F236-all.png", min_w=400)
        srch = capture("voicelog-search", EVID / "F236-search.png",
                       extra_env={"CLAUDESTUDIO_VOICELOG_QUERY": "security"}, min_w=400)
        assert allp and srch, "Voice-Log-Fenster fehlt"

        t_all = ocr(allp)
        t_srch = ocr(srch)
        OTHERS = ("guarded", "supervisor", "brain")
        n_all_others = sum(1 for k in OTHERS if k in t_all)
        # Ohne Filter: alle Interaktionen sichtbar (security + mehrere andere)
        assert "security" in t_all and n_all_others >= 2, f"nicht alle Einträge sichtbar: {t_all[:160]!r}"
        assert "4 treffer" in t_all or "4 treffer" in t_all.replace("  ", " "), f"Treffer-Anzahl falsch: {t_all[:120]!r}"
        # Mit Filter 'security': nur die Security-Interaktion
        assert "security" in t_srch, f"Treffer fehlt bei Suche: {t_srch[:160]!r}"
        assert "1 treffer" in t_srch, f"Suche nicht auf 1 Treffer gefiltert: {t_srch[:120]!r}"
        leaked = [k for k in OTHERS if k in t_srch]
        assert not leaked, f"Suche zeigt nicht passende Einträge: {leaked} ({t_srch[:160]!r})"
        record("F236", "pass", ev("F236", "voicelog-search.json",
               {"all_ocr": t_all[:220], "search_ocr": t_srch[:220],
                "screens": ["test-harness/evidence/F236-all.png", "test-harness/evidence/F236-search.png"]}),
               "VoiceLog: ohne Filter 4 Interaktionen, Suche 'security' filtert auf 1 passenden Eintrag (Volltext)")
    except Exception as e:
        record("F236", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
