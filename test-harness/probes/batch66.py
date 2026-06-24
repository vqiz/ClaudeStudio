#!/usr/bin/env python3
"""Verifikation Build-Batch 66 (echter Core, echtes Playwright/chromium):

  F338  Design-Mode visueller Feedback-Loop: die Akzentfarbe wird im Code auf #1A73E8 geändert; nach
        Browser-Reload misst der Checker am Element die CSS-Farbe = #1A73E8 (rgb 26,115,232).
"""
from __future__ import annotations
import json, subprocess, sys, tempfile, threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def main():
    proj = Path(tempfile.mkdtemp(prefix="cs-f338-"))
    (proj / "package.json").write_text('{"name":"f338","version":"1.0.0"}')
    subprocess.run(["npm", "install", "playwright", "--no-audit", "--no-fund", "--loglevel", "error"],
                   cwd=proj, capture_output=True, text=True, timeout=180)
    (proj / "index.html").write_text(
        "<!doctype html><html><head><meta charset='utf-8'><link rel='stylesheet' href='style.css'></head>"
        "<body><button id='cta'>Jetzt starten</button></body></html>\n")
    css = proj / "style.css"
    css.write_text("#cta { color: #888888; }\n")  # Ausgangsfarbe grau
    handler = partial(SimpleHTTPRequestHandler, directory=str(proj))
    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/index.html"

    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/b66.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=180)
        try:
            before = c.request("testing.measure_color", {"cwd": str(proj), "url": url, "selector": "#cta"})
            assert before["rgb"] != [26, 115, 232], f"schon vorher blau: {before}"
            # Design-Mode: Akzentfarbe im Code auf #1A73E8 ändern
            css.write_text("#cta { color: #1A73E8; }\n")
            after = c.request("testing.measure_color", {"cwd": str(proj), "url": url, "selector": "#cta"})
            assert after["rgb"] == [26, 115, 232], f"Farbe nach Reload != #1A73E8: {after}"
            record("F338", "pass", ev("F338", "design-mode-color.json", {"before": before, "after": after}),
                   f"Code-Farbänderung auf #1A73E8 nach Reload am Element gemessen (rgb {after['rgb']})")
        except Exception as e:
            record("F338", "fail", note=str(e))
        c.close()

    server.shutdown()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
