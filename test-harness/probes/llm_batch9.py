#!/usr/bin/env python3
"""Verifikation LLM-Batch 9 (echter Core, echtes Playwright + headless chromium):

  F324  E2E-Recorder: aus einer aufgezeichneten Klick-Sequenz auf einer localhost-Seite erzeugt der
        Recorder eine echte Playwright-Spec und führt sie HEADLESS gegen localhost aus — sie läuft grün.
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
    proj = Path(tempfile.mkdtemp(prefix="cs-f324-"))
    (proj / "package.json").write_text('{"name":"f324","version":"1.0.0"}')
    subprocess.run(["npm", "install", "playwright", "--no-audit", "--no-fund", "--loglevel", "error"],
                   cwd=proj, capture_output=True, text=True, timeout=180)
    # landing-page mit einer Klick-Interaktion
    (proj / "index.html").write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>Landing</title></head><body>"
        "<h1>Landing</h1>"
        "<button id='go' onclick=\"document.getElementById('out').textContent='DONE_F324'\">Go</button>"
        "<div id='out'>idle</div></body></html>\n")

    # lokaler Static-Server auf dem Projektverzeichnis
    handler = partial(SimpleHTTPRequestHandler, directory=str(proj))
    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/llm9.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=120)
        try:
            interactions = [
                {"action": "goto", "path": "/index.html"},
                {"action": "click", "selector": "#go"},
                {"action": "expect_text", "selector": "#out", "text": "DONE_F324"},
            ]
            r = c.request("testing.e2e_record", {
                "cwd": str(proj), "base_url": f"http://127.0.0.1:{port}", "interactions": interactions})
            assert "page.goto" in r["spec"] and "page.click" in r["spec"], "kein gültiges Playwright-Skript"
            assert r["green"] is True, f"Spec nicht grün (exit {r['exit']}): {r.get('stderr','')[:300]}"
            record("F324", "pass", ev("F324", "e2e-recorder.json",
                   {"green": True, "exit": r["exit"], "spec": r["spec"]}),
                   "Recorder erzeugte Playwright-Spec + lief headless grün gegen localhost")
        except Exception as e:
            record("F324", "fail", note=str(e))
        c.close()

    server.shutdown()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
