#!/usr/bin/env python3
"""Lokaler GitHub-REST-API-Substitut für die F250-Verifikation (kein github.com).

Implementiert exakt die Issue-Endpunkte, die der echte GitHub-MCP-Server bzw. die GitHub-REST-API
nutzt, inkl. Pflicht-Authentifizierung (`Authorization`-Header) — wie api.github.com (401 ohne Token).
Der Zustand (offen/geschlossen, fortlaufende Issue-Nummer) wird in-memory gehalten, sodass ein über
MCP angelegtes Issue später real geschlossen und der Zustandswechsel nachgelesen werden kann.

  POST  /repos/{owner}/{repo}/issues            {title, body}      -> 201 {number, state:"open", ...}
  PATCH /repos/{owner}/{repo}/issues/{number}   {state:"closed"}   -> 200 {number, state:"closed", ...}
  GET   /repos/{owner}/{repo}/issues/{number}                      -> 200 {number, state, title}
  GET   /health                                                    -> 200 (kein Auth)

Aufruf:  python3 github_api_mock.py <port>
"""
from __future__ import annotations
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

# Zustand: {"owner/repo": {number: {"number","title","body","state"}}}, fortlaufender Zähler je Repo.
ISSUES: dict[str, dict[int, dict]] = {}
COUNTER: dict[str, int] = {}

ISSUES_RE = re.compile(r"^/repos/([^/]+)/([^/]+)/issues$")
ISSUE_RE = re.compile(r"^/repos/([^/]+)/([^/]+)/issues/(\d+)$")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):  # still im Testlauf
        pass

    def _send(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self) -> bool:
        # api.github.com verlangt einen Token; ohne -> 401. Spiegelt den echten Auth-Fluss.
        return bool(self.headers.get("Authorization", "").strip())

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", "0") or "0")
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            return {}

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True})
            return
        if not self._auth_ok():
            self._send(401, {"message": "Requires authentication"})
            return
        m = ISSUE_RE.match(self.path)
        if m:
            key, num = f"{m.group(1)}/{m.group(2)}", int(m.group(3))
            issue = ISSUES.get(key, {}).get(num)
            if issue:
                self._send(200, issue)
            else:
                self._send(404, {"message": "Not Found"})
            return
        self._send(404, {"message": "Not Found"})

    def do_POST(self):
        if not self._auth_ok():
            self._send(401, {"message": "Requires authentication"})
            return
        m = ISSUES_RE.match(self.path)
        if not m:
            self._send(404, {"message": "Not Found"})
            return
        owner, repo = m.group(1), m.group(2)
        key = f"{owner}/{repo}"
        payload = self._read_json()
        COUNTER[key] = COUNTER.get(key, 0) + 1
        number = COUNTER[key]
        issue = {
            "number": number,
            "title": payload.get("title", ""),
            "body": payload.get("body", ""),
            "state": "open",
            "html_url": f"https://github.com/{owner}/{repo}/issues/{number}",
        }
        ISSUES.setdefault(key, {})[number] = issue
        self._send(201, issue)

    def do_PATCH(self):
        if not self._auth_ok():
            self._send(401, {"message": "Requires authentication"})
            return
        m = ISSUE_RE.match(self.path)
        if not m:
            self._send(404, {"message": "Not Found"})
            return
        key, num = f"{m.group(1)}/{m.group(2)}", int(m.group(3))
        issue = ISSUES.get(key, {}).get(num)
        if not issue:
            self._send(404, {"message": "Not Found"})
            return
        payload = self._read_json()
        if "state" in payload:
            issue["state"] = payload["state"]
        if "title" in payload:
            issue["title"] = payload["title"]
        self._send(200, issue)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
