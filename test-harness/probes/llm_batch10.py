#!/usr/bin/env python3
"""Verifikation LLM-Batch 10 (echter Core, ECHTER Playwright-MCP-Server, echtes chromium):

  F251  Echtes Playwright-MCP: ClaudeStudio (mcp.call_sequence) verbindet sich mit dem echten
        @playwright/mcp-Server, lässt ihn die localhost-landing-page öffnen und einen Screenshot
        machen; das zurückgegebene Screenshot-Artefakt ist ein echtes PNG.
"""
from __future__ import annotations
import base64, json, re, sys, tempfile, threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
EVID = ROOT / "test-harness" / "evidence"
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def find_png_bytes(result):
    """Sucht im MCP-Tool-Result nach Bilddaten (base64 image content) oder einem gespeicherten PNG-Pfad."""
    content = (result or {}).get("content") or []
    for c in content:
        if isinstance(c, dict) and c.get("type") == "image" and c.get("data"):
            try:
                return base64.b64decode(c["data"])
            except Exception:
                pass
    # sonst: Text mit Dateipfad
    for c in content:
        if isinstance(c, dict) and c.get("type") == "text":
            m = re.search(r"(/[^\s'\"]+\.(?:png|jpeg|jpg))", c.get("text", ""))
            if m and Path(m.group(1)).exists():
                return Path(m.group(1)).read_bytes()
    return None


def main():
    proj = Path(tempfile.mkdtemp(prefix="cs-f251-"))
    (proj / "index.html").write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>Landing</title>"
        "<style>body{background:#1A73E8;color:#fff;font-family:sans-serif;padding:60px}</style></head>"
        "<body><h1>ClaudeStudio Landing</h1><p>Playwright-MCP Screenshot-Test.</p></body></html>\n")
    handler = partial(SimpleHTTPRequestHandler, directory=str(proj))
    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/index.html"

    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/llm10.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=180)
        try:
            r = c.request("mcp.call_sequence", {
                "command": ["npx", "-y", "@playwright/mcp@latest", "--headless", "--isolated"],
                "calls": [
                    {"tool": "browser_navigate", "arguments": {"url": url}},
                    {"tool": "browser_take_screenshot", "arguments": {}},
                ]})
            assert len(r["results"]) == 2, f"unerwartete Ergebnisse: {r}"
            nav, shot = r["results"][0]["result"], r["results"][1]["result"]
            assert nav is not None, "navigate ohne Ergebnis"
            png = find_png_bytes(shot)
            assert png and len(png) > 2000, f"kein echtes Screenshot-PNG (len={len(png) if png else 0})"
            assert png[:8] == b"\x89PNG\r\n\x1a\n" or png[:3] == b"\xff\xd8\xff", "kein gültiges Bildformat"
            (EVID / "F251").mkdir(parents=True, exist_ok=True)
            (EVID / "F251" / "screenshot.png").write_bytes(png)
            from PIL import Image
            im = Image.open(EVID / "F251" / "screenshot.png")
            assert im.size[0] > 100 and im.size[1] > 100, f"Screenshot zu klein: {im.size}"
            record("F251", "pass", ev("F251", "playwright-mcp.json",
                   {"server": r.get("server"), "screenshot_bytes": len(png), "image_size": im.size,
                    "navigate_ok": True}),
                   f"Playwright-MCP steuerte echten Browser; Screenshot {im.size} ({len(png)} bytes) gespeichert")
        except Exception as e:
            record("F251", "fail", note=str(e))
        c.close()

    server.shutdown()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
