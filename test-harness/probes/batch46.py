#!/usr/bin/env python3
"""Verifikation Build-Batch 46 (echter Core, echtes curl/HTTP, kein Mock der Sync-Logik):

  F357  GitHub-Issues-Sync bidirektional: ein Task ohne Issue erzeugt beim Sync ein Issue
        (POST), die Nummer wird gemerkt; wird das Issue geschlossen, markiert ein erneuter
        Sync den lokalen Task als 'closed' (GET-Reconcile).

Die GitHub-REST-API wird durch einen lokalen Server mit GitHub-Issue-Shape ersetzt (gleiches
Lokal-Substitut-Muster wie beim Slack-Webhook); getestet wird die echte Sync-/Reconcile-Logik
+ echte HTTP-Calls des Core via curl.
"""
from __future__ import annotations
import json, re, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}
ISSUES: dict[int, dict] = {}
NEXT = {"n": 1}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


class GitHubMock(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode() if n else "{}"
        m_close = re.match(r"^/_close/(\d+)$", self.path)
        if m_close:  # Test-Helfer: Issue schließen
            num = int(m_close.group(1))
            ISSUES.get(num, {}).update(state="closed")
            return self._send(200, {"number": num, "state": "closed"})
        if self.path.endswith("/issues"):  # Issue anlegen
            num = NEXT["n"]; NEXT["n"] += 1
            title = json.loads(body or "{}").get("title", "")
            ISSUES[num] = {"number": num, "state": "open", "title": title}
            return self._send(201, ISSUES[num])
        self._send(404, {"message": "not found"})

    def do_GET(self):
        m = re.match(r"^/repos/.+/issues/(\d+)$", self.path)
        if m:
            num = int(m.group(1))
            return self._send(200, ISSUES.get(num, {"number": num, "state": "open"}))
        self._send(404, {"message": "not found"})

    def log_message(self, *a):
        pass


def main():
    server = HTTPServer(("127.0.0.1", 0), GitHubMock)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b46.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=30)
        try:
            repo = "acme/todo-api"
            task = {"id": "t1", "title": "Add health endpoint"}
            # 1) Sync: Task ohne Issue -> Issue wird angelegt + Nummer gemerkt
            r1 = c.request("integrations.github_sync",
                           {"repo": repo, "api_base": base, "tasks": [task]})
            t1 = r1["tasks"][0]
            assert t1.get("issue_number") and t1["state"] == "open", t1
            assert any(e["op"] == "create_issue" for e in r1["log"])
            issue_n = t1["issue_number"]

            # 2) Issue auf "GitHub" schließen
            import urllib.request
            urllib.request.urlopen(urllib.request.Request(f"{base}/_close/{issue_n}", data=b"{}")).read()

            # 3) erneuter Sync -> Reconcile markiert den lokalen Task als closed
            r2 = c.request("integrations.github_sync",
                           {"repo": repo, "api_base": base, "tasks": [t1]})
            t2 = r2["tasks"][0]
            assert t2["state"] == "closed", t2
            assert any(e["op"] == "reconcile" and e["state"] == "closed" for e in r2["log"])

            record("F357", "pass", ev("F357", "github-sync.json",
                   {"after_create": t1, "after_close_resync": t2,
                    "create_log": r1["log"], "reconcile_log": r2["log"]}),
                   f"Sync legte Issue #{issue_n} an; nach Schließen markiert Re-Sync den Task als closed")
        except Exception as e:
            record("F357", "fail", note=str(e))
        c.close()

    server.shutdown()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
