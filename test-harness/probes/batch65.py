#!/usr/bin/env python3
"""Verifikation Build-Batch 65 (echter Core, echtes Playwright/chromium):

  F340  Responsive-Checker: rendert eine Seite mit einem 1200px breiten Element bei 375/768/1440px und
        meldet horizontalen Overflow bei 375px, aber keinen bei 1440px.
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
    proj = Path(tempfile.mkdtemp(prefix="cs-f340-"))
    (proj / "package.json").write_text('{"name":"f340","version":"1.0.0"}')
    subprocess.run(["npm", "install", "playwright", "--no-audit", "--no-fund", "--loglevel", "error"],
                   cwd=proj, capture_output=True, text=True, timeout=180)
    (proj / "index.html").write_text(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>*{margin:0}body{font-family:sans-serif}</style></head><body>"
        "<h1>Landing</h1>"
        "<div style='width:1200px;height:60px;background:#1A73E8;color:#fff'>Breites Element (1200px)</div>"
        "</body></html>\n")
    handler = partial(SimpleHTTPRequestHandler, directory=str(proj))
    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/b65.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=180)
        try:
            r = c.request("testing.responsive_check", {
                "cwd": str(proj), "url": f"http://127.0.0.1:{port}/index.html",
                "widths": [375, 768, 1440]})
            vps = {v["width"]: v for v in r["viewports"]}
            assert vps[375]["overflow"] is True, f"375px sollte überlaufen: {vps.get(375)}"
            assert vps[1440]["overflow"] is False, f"1440px sollte NICHT überlaufen: {vps.get(1440)}"
            record("F340", "pass", ev("F340", "responsive-check.json", {"viewports": r["viewports"]}),
                   f"Overflow@375={vps[375]['scroll_width']}>{vps[375]['client_width']} ja; @1440 nein")
        except Exception as e:
            record("F340", "fail", note=str(e))
        c.close()

    server.shutdown()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
