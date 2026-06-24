#!/usr/bin/env python3
"""Verifikation UI-Batch 26 (echte SwiftUI-View via ImageRenderer, kein Mock):

  F029  Tab-Wechsel innerhalb eines Projekts erhält den State des verlassenen Tabs. Die Tab-Inhalte
        werden über einen `switch` gerendert (Tab-View wird beim Wechsel NEU erzeugt — eine naive
        @State würde verloren gehen); der Pro-Tab-State liegt in einem @Observable-Modell und überlebt
        den Wechsel. Nachweis über drei ImageRenderer-Renderings mit EINEM gemeinsamen Modell:
        A (Eingabe X) → B → A — das dritte A-Rendering zeigt wieder X. Screenshots F029-{1A,2B,3A}.png.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
EVID = ROOT / "test-harness" / "evidence"
APP = ROOT / "app" / ".build" / "debug" / "ClaudeStudio"
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def ocr(png: Path) -> str:
    return subprocess.run(["tesseract", str(png), "stdout", "--psm", "6"],
                          capture_output=True, text=True).stdout.replace("\n", " ")


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    try:
        (EVID / "F029").mkdir(parents=True, exist_ok=True)
        outdir = EVID / "F029"
        subprocess.run([str(APP)], env={**os.environ, "CLAUDESTUDIO_RENDER_TABRETENTION": str(outdir)},
                       capture_output=True, timeout=30)
        a1, b2, a3 = outdir / "1-A.png", outdir / "2-B.png", outdir / "3-A.png"
        assert a1.exists() and b2.exists() and a3.exists(), "Renderings fehlen"
        t1, t2, t3 = ocr(a1), ocr(b2), ocr(a3)
        # Tab A initial mit Eingabe X
        assert "eingabe-a-77" in t1.lower().replace(" ", ""), f"Tab A initial fehlt: {t1!r}"
        # Tab B zwischendurch (anderer State)
        assert "tab b" in t2.lower() and "eingabe-b-08" in t2.lower().replace(" ", ""), f"Tab B fehlt: {t2!r}"
        # Tab A nach Rückkehr: State X ERHALTEN (überlebt den B-Wechsel)
        assert "eingabe-a-77" in t3.lower().replace(" ", ""), f"Tab-A-State nach Wechsel NICHT erhalten: {t3!r}"
        assert "eingabe-b-08" not in t3.lower().replace(" ", ""), f"Tab A zeigt B's State: {t3!r}"
        record("F029", "pass", ev("F029", "tab-retention.json",
               {"render_1_tabA": t1[:120], "render_2_tabB": t2[:120], "render_3_tabA": t3[:120],
                "screens": [f"test-harness/evidence/F029/{n}" for n in ("1-A.png", "2-B.png", "3-A.png")]}),
               "Tab-A-State (EINGABE-A-77) überlebt den Wechsel A→B→A (drittes A-Rendering = erstes)")
    except Exception as e:
        record("F029", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
