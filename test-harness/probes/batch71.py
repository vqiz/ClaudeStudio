#!/usr/bin/env python3
"""Verifikation Build-Batch 71 (echter Core, ECHTER MCP-Transport, ECHTER claude, echtes Playwright):

  F337  Figma-to-Code: der Core liest ein Figma-Frame über einen echten MCP-Server (initialize +
        tools/call) und lässt den echten claude daraus Komponenten-Code (HTML/CSS) erzeugen; die
        gerenderte Komponente übernimmt Layout, Texte und Farben des Frames im Browser.

Der Figma-MCP-Server wird durch einen lokalen stdio-JSON-RPC-MCP-Substitut ersetzt, der einen
Frame im Figma-Knoten-Shape liefert (gleiches MCP-Substitut-Muster wie beim Browser-Agent/
mcp.call_tool). Figma-Token bleibt extern; getestet wird der echte MCP-Roundtrip + echte Codegen.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
CLAUDE = os.path.expanduser("~/.local/bin/claude")
results: dict[str, dict] = {}

FRAME = {
    "name": "Dashboard", "type": "FRAME",
    "children": [
        {"name": "Header", "type": "FRAME", "fills": [{"color": "#1A73E8"}], "children": [
            {"name": "Title", "type": "TEXT", "characters": "Mein Dashboard",
             "fills": [{"color": "#FFFFFF"}]}]},
        {"name": "Card", "type": "FRAME", "fills": [{"color": "#FFFFFF"}], "children": [
            {"name": "Label", "type": "TEXT", "characters": "Umsatz heute"},
            {"name": "Value", "type": "TEXT", "characters": "1.234 EUR"}]},
        {"name": "Button", "type": "INSTANCE", "fills": [{"color": "#1A73E8"}], "children": [
            {"name": "BtnLabel", "type": "TEXT", "characters": "Aktualisieren",
             "fills": [{"color": "#FFFFFF"}]}]},
    ],
}

# Minimaler stdio-JSON-RPC-MCP-Server (Figma-Substitut) — eine Antwortzeile pro Request.
MCP_SERVER = r'''
import sys, json
FRAME = %s
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except Exception:
        continue
    m = req.get("method"); rid = req.get("id")
    if m == "initialize":
        res = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
               "serverInfo": {"name": "figma-mock", "version": "1"}}
    elif m == "tools/list":
        res = {"tools": [{"name": "get_frame", "description": "Read a Figma frame",
                          "inputSchema": {"type": "object", "properties": {
                              "file_key": {"type": "string"}, "node_id": {"type": "string"}}}}]}
    elif m == "tools/call":
        res = {"content": [{"type": "text", "text": json.dumps(FRAME, ensure_ascii=False)}]}
    else:
        res = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": res}) + "\n")
    sys.stdout.flush()
''' % json.dumps(FRAME, ensure_ascii=False)


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def main():
    proj = Path(tempfile.mkdtemp(prefix="cs-f337-"))
    (proj / "figma_mcp.py").write_text(MCP_SERVER)
    (proj / "package.json").write_text('{"name":"f337","version":"1.0.0"}')
    subprocess.run(["npm", "install", "playwright", "--no-audit", "--no-fund", "--loglevel", "error"],
                   cwd=proj, capture_output=True, text=True, timeout=180)

    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/b71.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": CLAUDE}) as ctx:
        c = P.Client(ctx["sock"], timeout=600)
        try:
            r = c.request("ui.figma_to_code", {
                "cwd": str(proj),
                "command": "python3", "args": [str(proj / "figma_mcp.py")],
                "frame_tool": "get_frame",
                "arguments": {"file_key": "ABC123", "node_id": "1:2"}})
            # MCP-Frame muss echt geflossen sein
            assert "Mein Dashboard" in (r.get("frame") or ""), "Frame nicht über MCP empfangen"
            html = r.get("html") or ""
            assert html.strip(), "kein HTML generiert"

            check = ("import { chromium } from 'playwright';\n"
                     "const b = await chromium.launch({headless:true});\n"
                     "const p = await b.newPage();\n"
                     f"await p.goto('file://{proj}/index.html');\n"
                     "const r = await p.evaluate(() => {\n"
                     "  const txt = document.body.innerText;\n"
                     "  const bgs = [...document.querySelectorAll('*')].map(e=>getComputedStyle(e).backgroundColor);\n"
                     "  const cols = [...document.querySelectorAll('*')].map(e=>getComputedStyle(e).color);\n"
                     "  return {\n"
                     "    header: !!document.querySelector('header, h1, h2'),\n"
                     "    card: !!document.querySelector('.card, [class*=card], article'),\n"
                     "    button: !!document.querySelector('button, [class*=btn]'),\n"
                     "    txt,\n"
                     "    accent: bgs.concat(cols).includes('rgb(26, 115, 232)'),\n"
                     "  };\n"
                     "});\n"
                     "await b.close(); console.log(JSON.stringify(r));\n")
            (proj / "check.mjs").write_text(check)
            out = subprocess.run(["node", "check.mjs"], cwd=proj, capture_output=True, text=True, timeout=90)
            dom = json.loads(out.stdout.strip().splitlines()[-1])
            assert dom["header"] and dom["card"] and dom["button"], f"Struktur fehlt: {dom}"
            texts_ok = all(t in dom["txt"] for t in ("Mein Dashboard", "Umsatz heute", "Aktualisieren"))
            assert texts_ok, f"Texte nicht übernommen: {dom['txt'][:200]!r}"
            assert dom["accent"], "Akzentfarbe #1A73E8 nicht übernommen"
            record("F337", "pass", ev("F337", "figma-to-code.json",
                   {"rendered": {k: dom[k] for k in ("header", "card", "button", "accent")},
                    "texts_present": texts_ok, "frame_excerpt": (r["frame"])[:300],
                    "html_excerpt": html[:600]}),
                   "MCP-Frame gelesen; claude erzeugte Komponente mit Layout+Texten+Akzentfarbe #1A73E8")
        except Exception as e:
            record("F337", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
