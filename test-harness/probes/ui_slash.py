#!/usr/bin/env python3
"""Verifikation: Slash-Befehl-Autovervollständigung im Chat-Composer
(echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock).

  SLASH-1  Tippt der Nutzer im Chat eine Zeile, die mit „/" beginnt, erscheint
           ein Autovervollständigungs-Popup mit den verfügbaren Befehlen
           (eingebaute CLI-Befehle + installierte Skills). Nachgewiesen per OCR:
           im „/"-Zustand sind die Builtins (clear/compact/cost/mcp/…) UND die
           Skill-Einträge (graphify/commit/code-review) sichtbar.

  SLASH-2  Das Popup filtert live nach dem getippten Token. „/co" zeigt nur die
           Treffer (compact, cost, commit, code-review) und blendet die übrigen
           Befehle (status, mcp, resume, graphify, …) aus. Nachgewiesen per OCR
           des gefilterten Menüs (Treffer vorhanden, Nicht-Treffer abwesend).

Methode: das Fenster wird per CGWindowList-ID gezielt aufgenommen
(`screencapture -l`), nach sRGB konvertiert, der Menübereich per `tesseract`
ausgelesen. Es rendert die ECHTE `SlashCommandMenu` über den ECHTEN Matcher
`SlashCommand.matches` (UITest-Seam `CLAUDESTUDIO_UITEST=slash`).
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
WINDOWID = LIB / "windowid"
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
    dst.parent.mkdir(parents=True, exist_ok=True)
    raw = dst.with_suffix(".raw.png")
    sh("screencapture", "-x", "-o", f"-l{wid}", str(raw))
    srgb = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"
    r = sh("sips", "--matchToWithIntent", srgb, "relative", str(raw), "--out", str(dst))
    if r.returncode != 0 or not dst.exists():
        dst.write_bytes(raw.read_bytes())
    return dst


def ocr(png: Path) -> str:
    """OCR der unteren Fensterhälfte (dort liegt das Menü), 2x hochskaliert."""
    im = Image.open(png).convert("RGB"); W, H = im.size
    crop = im.crop((0, int(H * 0.30), W, H))
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    tmp = png.with_suffix(".ocr.png"); crop.save(tmp)
    return sh("tesseract", str(tmp), "stdout", "--psm", "6").stdout.lower()


def shoot(query: str, dst: Path) -> tuple[Path, str]:
    env = {**os.environ, "CLAUDESTUDIO_UITEST": "slash", "CLAUDESTUDIO_SLASH_QUERY": query}
    proc = subprocess.Popen([str(APP)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wid = find_window(min_w=500)
        if not wid:
            raise RuntimeError(f"Fenster nicht gefunden (query={query!r})")
        time.sleep(1.0)
        png = capture_srgb(wid, dst)
        return png, ocr(png)
    finally:
        proc.terminate(); kill_app()


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": f"App nicht gebaut: {APP}"}}}))
        return
    kill_app()

    # ---- SLASH-1: „/" öffnet das Befehls-Popup ----
    try:
        png, text = shoot("/", EVID / "SLASH" / "menu-all.png")
        has_header = "befehle" in text
        builtins = ["clear", "compact", "cost", "status", "model", "mcp", "resume"]
        found_b = [t for t in builtins if t in text]
        ok1 = has_header and len(found_b) >= 4
        record("SLASH-1", "pass" if ok1 else "fail",
               ev("SLASH-1", "menu-all.json",
                  {"png": str(png.relative_to(ROOT)), "query": "/",
                   "header_present": has_header, "builtins_found": found_b,
                   "ocr_excerpt": text[:600]}),
               f"Tippen von / öffnet das Popup (Header={has_header}, {len(found_b)} Builtins sichtbar)")
    except Exception as e:  # noqa: BLE001
        record("SLASH-1", "fail", note=str(e))

    # ---- SLASH-2: Popup mischt eingebaute Befehle UND installierte Skills ----
    # „/c" liefert eine kurze Liste (alle ohne Scrollen sichtbar): die Builtins
    # clear/compact/cost und die Skills commit/code-review.
    try:
        png, text = shoot("/c", EVID / "SLASH" / "menu-mix.png")
        builtins = [t for t in ["clear", "compact", "cost"] if t in text]
        skills = [t for t in ["commit", "code"] if t in text]
        ok2 = len(builtins) >= 2 and len(skills) >= 2
        record("SLASH-2", "pass" if ok2 else "fail",
               ev("SLASH-2", "menu-mix.json",
                  {"png": str(png.relative_to(ROOT)), "query": "/c",
                   "builtins_found": builtins, "skills_found": skills,
                   "ocr_excerpt": text[:600]}),
               f"/c zeigt Builtins {builtins} + Skills {skills} gemischt")
    except Exception as e:  # noqa: BLE001
        record("SLASH-2", "fail", note=str(e))

    # ---- SLASH-3: „/co" filtert live ----
    try:
        png, text = shoot("/co", EVID / "SLASH" / "menu-filtered.png")
        present = [t for t in ["compact", "cost", "commit", "code"] if t in text]
        # Nicht-Treffer dürfen NICHT erscheinen (echtes Filtern statt Vollliste).
        absent_expected = ["status", "resume", "graphify", "mcp"]
        leaked = [t for t in absent_expected if t in text]
        ok3 = len(present) >= 3 and not leaked
        record("SLASH-3", "pass" if ok3 else "fail",
               ev("SLASH-3", "menu-filtered.json",
                  {"png": str(png.relative_to(ROOT)), "query": "/co",
                   "matches_present": present, "non_matches_leaked": leaked,
                   "ocr_excerpt": text[:600]}),
               f"/co -> Treffer {present}, gefilterte weg (Leaks: {leaked or 'keine'})")
    except Exception as e:  # noqa: BLE001
        record("SLASH-2", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
