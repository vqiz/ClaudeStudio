#!/usr/bin/env python3
"""Verifikation LLM-Batch 17 (echter Core, ECHTER claude Vision, echtes Playwright):

  F336  Screenshot-to-Code: aus einem hochgeladenen UI-Screenshot (Header, Karte, Button) generiert der
        echte claude (Vision) HTML/CSS; das gerenderte Ergebnis enthält Header, Karte und Button.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
CLAUDE = os.path.expanduser("~/.local/bin/claude")
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def make_layout_png(path: Path):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (800, 600), "#f0f0f0")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 800, 80], fill="#1A73E8")          # Header oben
    d.text((30, 30), "HEADER", fill="white")
    d.rectangle([150, 180, 650, 430], fill="white", outline="#cccccc", width=2)  # Karte
    d.text((180, 210), "Card", fill="#333333")
    d.rectangle([320, 470, 480, 530], fill="#1A73E8")     # Button
    d.text((370, 495), "Button", fill="white")
    img.save(path)


def main():
    proj = Path(tempfile.mkdtemp(prefix="cs-f336-"))
    make_layout_png(proj / "layout.png")
    (proj / "package.json").write_text('{"name":"f336","version":"1.0.0"}')
    subprocess.run(["npm", "install", "playwright", "--no-audit", "--no-fund", "--loglevel", "error"],
                   cwd=proj, capture_output=True, text=True, timeout=180)

    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/llm17.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": CLAUDE}) as ctx:
        c = P.Client(ctx["sock"], timeout=600)
        try:
            r = c.request("agents.screenshot_to_code", {"cwd": str(proj), "image": "layout.png"})
            html = r["html"]
            assert html.strip(), "kein HTML generiert"
            # gerendertes Ergebnis prüfen (file:// via Playwright)
            check = ("import { chromium } from 'playwright';\n"
                     "const b = await chromium.launch({headless:true});\n"
                     "const p = await b.newPage();\n"
                     f"await p.goto('file://{proj}/index.html');\n"
                     "const r = await p.evaluate(() => ({\n"
                     "  header: !!document.querySelector('header, h1, h2'),\n"
                     "  button: !!document.querySelector('button, input[type=submit], [class*=btn]'),\n"
                     "  card: !!document.querySelector('.card, [class*=card], article'),\n"
                     "}));\n"
                     "await b.close(); console.log(JSON.stringify(r));\n")
            (proj / "check.mjs").write_text(check)
            out = subprocess.run(["node", "check.mjs"], cwd=proj, capture_output=True, text=True, timeout=90)
            dom = json.loads(out.stdout.strip().splitlines()[-1])
            assert dom["header"] and dom["card"] and dom["button"], f"gerendert fehlt etwas: {dom}"
            record("F336", "pass", ev("F336", "screenshot-to-code.json",
                   {"rendered": dom, "html_excerpt": html[:600]}),
                   "Vision-Agent erzeugte HTML aus Screenshot; gerendert: Header+Karte+Button vorhanden")
        except Exception as e:
            record("F336", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
