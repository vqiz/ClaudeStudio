#!/usr/bin/env python3
"""Verifikation Build-Batch 77 (echter Core, echtes npm install, kein Mock der Manager-Logik):

  F255  Plugin-Manager installiert MCP-Bundles via npm / lokal / URL, unterstützt Update + Deinstall
        und erkennt den Transport (stdio vs remote) je Bundle automatisch aus dem Manifest:
        ein per npm installiertes Bundle erscheint nach Install und verschwindet nach Deinstall;
        command-Bundles ⇒ stdio, url-Bundles ⇒ remote.
"""
from __future__ import annotations
import json, sys, tempfile
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
    ws = Path(tempfile.mkdtemp(prefix="cs-f255-"))
    # A) lokales stdio-Bundle (Manifest mit command)
    a = ws / "local_stdio"; a.mkdir()
    (a / "mcp.json").write_text(json.dumps(
        {"name": "local-stdio", "command": "node", "args": ["server.js"]}))
    # B) npm-Bundle (package.json.mcp mit command) -> stdio
    b = ws / "npm_pkg"; b.mkdir()
    (b / "package.json").write_text(json.dumps(
        {"name": "my-mcp-plugin", "version": "0.1.0",
         "mcp": {"command": "node", "args": ["index.js"]}}))
    (b / "index.js").write_text("// MCP stdio server\n")
    # C) lokales remote-Bundle (Manifest mit url) -> remote (Transport aus Manifest, nicht aus Kind)
    d = ws / "local_remote"; d.mkdir()
    (d / "mcp.json").write_text(json.dumps(
        {"name": "local-remote", "url": "https://remote.example.com/mcp"}))

    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b74.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=120)
        try:
            # vorhandene Test-Bundles bereinigen (idempotent)
            for n in ("local-stdio", "my-mcp-plugin", "local-remote", "remote-bundle"):
                c.request("mcp_plugin.uninstall", {"name": n})

            i_local = c.request("mcp_plugin.install", {"source": str(a), "kind": "local"})
            i_npm = c.request("mcp_plugin.install", {"source": str(b), "kind": "npm"})
            i_url = c.request("mcp_plugin.install",
                              {"source": "https://mcp.example.com/sse", "kind": "url",
                               "name": "remote-bundle"})
            i_lrem = c.request("mcp_plugin.install", {"source": str(d), "kind": "local"})

            # Transport-Auto-Erkennung je Bundle
            assert i_local["transport"] == "stdio", i_local
            assert i_npm["transport"] == "stdio" and i_npm["name"] == "my-mcp-plugin", i_npm
            assert i_url["transport"] == "remote", i_url
            assert i_lrem["transport"] == "remote", i_lrem  # aus Manifest-url, obwohl kind=local

            # npm-Bundle erscheint nach Install
            lst = {e["name"]: e for e in c.request("mcp_plugin.list")["plugins"]}
            assert "my-mcp-plugin" in lst, f"npm-Bundle nicht in Liste: {list(lst)}"

            # Update funktioniert
            upd = c.request("mcp_plugin.update", {"name": "my-mcp-plugin"})
            assert upd["ok"], upd

            # Deinstall -> npm-Bundle verschwindet
            un = c.request("mcp_plugin.uninstall", {"name": "my-mcp-plugin"})
            assert un["removed"], un
            lst2 = {e["name"]: e for e in c.request("mcp_plugin.list")["plugins"]}
            assert "my-mcp-plugin" not in lst2, f"npm-Bundle nach Deinstall noch da: {list(lst2)}"

            record("F255", "pass", ev("F255", "plugin-manager.json",
                   {"install_log_npm": i_npm["log"],
                    "transports": {"local-stdio": i_local["transport"], "my-mcp-plugin": i_npm["transport"],
                                   "remote-bundle": i_url["transport"], "local-remote": i_lrem["transport"]},
                    "after_install": sorted(lst), "after_uninstall": sorted(lst2),
                    "update_ok": upd["ok"]}),
                   "npm-Bundle install→sichtbar, deinstall→weg; Transport je Bundle erkannt (stdio×2, remote×2)")
        except Exception as e:
            record("F255", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
