#!/usr/bin/env python3
"""Verifikation UI-Batch 16 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F145  Active-Context-Bar zeigt die aktuell geladenen Kontext-Blöcke (Dateien/Tools/Memory) mit ihrem
        jeweiligen Token-Anteil. Der ECHTE ContextBar rendert die geseedeten Blöcke als proportionalen
        Balken + Liste mit Token-Zahlen; die Gesamtsumme (2400) stimmt. Per OCR nachgewiesen.
        Screenshot F145-context.png.
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


def ocr(png: Path) -> str:
    im = Image.open(png).convert("RGB"); W, H = im.size
    crop = im.crop((int(W * 0.28), int(H * 0.14), int(W * 0.74), int(H * 0.82)))
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    tmp = png.with_suffix(".ocr.png"); crop.save(tmp)
    return sh("tesseract", str(tmp), "stdout", "--psm", "6").stdout.lower()


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    try:
        (EVID / "F145").mkdir(parents=True, exist_ok=True)
        png = capture("context", EVID / "F145-context.png", min_w=300)
        assert png, "Context-Fenster fehlt"
        t = ocr(png)
        flat = t.replace(" ", "")
        # Zahlen-Variante: Tausender-Trennzeichen (deutsch: 2.400) entfernen.
        nums = re.sub(r"(?<=\d)[.,](?=\d)", "", flat)
        assert "active" in t and "context" in t, f"Header fehlt: {t[:160]!r}"
        # Kontext-Blöcke (Dateien/Tools/Memory)
        names = [n for n in ("index.js", "readme", "bash", "read", "notizen") if n in flat]
        assert len(names) >= 3, f"zu wenige Kontext-Blöcke erkannt: {names} ({t[:200]!r})"
        # Token-Anteile + Gesamtsumme (mit/ohne Tausender-Punkt)
        toks = [v for v in ("1200", "800", "300") if v in nums]
        assert len(toks) >= 2, f"Token-Anteile fehlen: {toks} ({t[:200]!r})"
        assert "tok" in t, f"Token-Einheit fehlt: {t[:160]!r}"
        assert "2400" in nums, f"Gesamtsumme (2400) fehlt: {t[:200]!r}"
        record("F145", "pass", ev("F145", "context-bar.json",
               {"ocr": t[:280], "blocks_seen": names, "tokens_seen": toks,
                "screen": "test-harness/evidence/F145-context.png"}),
               f"Active-Context-Bar: Blöcke {names} mit Token-Anteilen {toks}, Gesamt 2400 tokens")
    except Exception as e:
        record("F145", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
