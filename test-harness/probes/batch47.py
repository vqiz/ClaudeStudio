#!/usr/bin/env python3
"""Verifikation Build-Batch 47 (echter Core, echtes curl/HTTP):

  F285  Admin-API Usage-Report: der Core ruft GET /v1/organizations/usage_report/claude_code mit
        dem hinterlegten Admin-Key auf und parst die Antwort — HTTP 200 + die Report-Werte
        (Tokens/Kosten). Ohne Key antwortet die API 401.

Die Anthropic-Admin-API wird durch einen lokalen Server mit dem dokumentierten Report-Shape ersetzt
(Lokal-Substitut wie bei Slack/GitHub); getestet werden der echte HTTP-Abruf + das Parsen.
"""
from __future__ import annotations
import json, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
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


class AdminMock(BaseHTTPRequestHandler):
    def do_GET(self):
        if not self.path.endswith("/usage_report/claude_code"):
            return self._send(404, {"error": "not found"})
        if not self.headers.get("x-api-key"):
            return self._send(401, {"error": {"type": "authentication_error"}})
        report = {"data": [{"date": "2026-06-22", "actor": "user@acme",
                            "core_metrics": {"num_sessions": 12, "lines_added": 540}}],
                  "totals": {"total_tokens": 184320, "total_cost_usd": 4.27}}
        self._send(200, report)

    def _send(self, code, obj):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj, separators=(",", ":")).encode())  # einzeilig

    def log_message(self, *a):
        pass


def main():
    server = HTTPServer(("127.0.0.1", 0), AdminMock)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b47.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=30)
        try:
            r = c.request("integrations.usage_report", {"api_base": base, "admin_key": "sk-ant-admin-XXX"})
            assert r["http_status"] == 200, r
            assert r["authenticated"] is True
            totals = r["report"]["totals"]
            assert totals["total_tokens"] == 184320 and totals["total_cost_usd"] == 4.27
            assert r["report"]["data"][0]["core_metrics"]["num_sessions"] == 12
            # ohne Key -> 401
            r401 = c.request("integrations.usage_report", {"api_base": base, "admin_key": ""})
            assert r401["http_status"] == 401, r401
            record("F285", "pass", ev("F285", "usage-report.json",
                   {"with_key": {"http_status": r["http_status"], "totals": totals},
                    "without_key_status": r401["http_status"]}),
                   "GET usage_report/claude_code: HTTP 200 + Tokens/Kosten geparst; ohne Key 401")
        except Exception as e:
            record("F285", "fail", note=str(e))
        c.close()

    server.shutdown()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
