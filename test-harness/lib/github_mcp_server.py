#!/usr/bin/env python3
"""Echter GitHub-MCP-Server (stdio, newline-delimited JSON-RPC, MCP 2024-11-05) für F250.

Spricht dasselbe MCP-Protokoll wie der offizielle GitHub-MCP-Server (initialize / notifications/
initialized / tools/list / tools/call) und ruft pro Tool die ECHTE GitHub-REST-API auf — Basis-URL
über `GITHUB_API_BASE`, Token über `GITHUB_TOKEN` (im Test zeigt die Basis auf den lokalen
GitHub-API-Substituten; im Betrieb auf https://api.github.com). Genau dieser Server wird vom
echten MCP-Client des Cores (`mcp.call_sequence`) über stdio gesteuert.

Tools:
  create_issue {repo, title, body}   -> POST   {base}/repos/{repo}/issues
  close_issue  {repo, number}        -> PATCH  {base}/repos/{repo}/issues/{number}  {state:"closed"}
  get_issue    {repo, number}        -> GET    {base}/repos/{repo}/issues/{number}
Jedes Tool gibt das GitHub-JSON als Text-Content zurück (MCP `content:[{type:"text",text:...}]`).
"""
from __future__ import annotations
import json
import os
import sys
import urllib.request

BASE = os.environ.get("GITHUB_API_BASE", "https://api.github.com").rstrip("/")
TOKEN = os.environ.get("GITHUB_TOKEN", "")

TOOLS = [
    {"name": "create_issue", "description": "Create a GitHub issue",
     "inputSchema": {"type": "object", "required": ["repo", "title"],
                     "properties": {"repo": {"type": "string"}, "title": {"type": "string"},
                                    "body": {"type": "string"}}}},
    {"name": "close_issue", "description": "Close a GitHub issue",
     "inputSchema": {"type": "object", "required": ["repo", "number"],
                     "properties": {"repo": {"type": "string"}, "number": {"type": "integer"}}}},
    {"name": "get_issue", "description": "Get a GitHub issue",
     "inputSchema": {"type": "object", "required": ["repo", "number"],
                     "properties": {"repo": {"type": "string"}, "number": {"type": "integer"}}}},
]


def _api(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    if TOKEN:
        req.add_header("Authorization", f"token {TOKEN}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode() or "{}")


def call_tool(name: str, args: dict) -> dict:
    repo = args.get("repo", "")
    if name == "create_issue":
        return _api("POST", f"/repos/{repo}/issues",
                    {"title": args.get("title", ""), "body": args.get("body", "")})
    if name == "close_issue":
        return _api("PATCH", f"/repos/{repo}/issues/{int(args['number'])}", {"state": "closed"})
    if name == "get_issue":
        return _api("GET", f"/repos/{repo}/issues/{int(args['number'])}")
    raise ValueError(f"unknown tool: {name}")


def send(obj: dict):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method, mid = msg.get("method"), msg.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "github-mcp-fixture", "version": "1.0.0"}}})
        elif method == "notifications/initialized":
            pass  # Notification: keine Antwort
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg.get("params", {})
            try:
                result = call_tool(params.get("name", ""), params.get("arguments", {}))
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": json.dumps(result)}], "isError": False}})
            except Exception as e:
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": f"error: {e}"}], "isError": True}})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32601, "message": f"method not found: {method}"}})


if __name__ == "__main__":
    main()
