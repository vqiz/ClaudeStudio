#!/usr/bin/env python3
"""Verifikation Build-Batch 48 (echter Core, echtes Shell + curl/HTTP):

  F358  Slack-Bot-Modus: ein Slack-Befehl löst einen Task aus; ClaudeStudio führt den Task (echtes
        Shell-Kommando) aus und postet das echte Ergebnis als Slack-Nachricht an die response_url
        zurück (in_channel). Der lokale Slack-Server empfängt die Ergebnis-Nachricht.
"""
from __future__ import annotations
import json, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}
received: list[dict] = []


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


class SlackMock(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            received.append(json.loads(self.rfile.read(n).decode()))
        except Exception:
            received.append({})
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a):
        pass


def main():
    server = HTTPServer(("127.0.0.1", 0), SlackMock)
    response_url = f"http://127.0.0.1:{server.server_address[1]}/response"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b48.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=30)
        try:
            proj = Path(tempfile.mkdtemp(prefix="cs-b48-"))
            (proj / "app.js").write_text("// TODO a\nconst x=1;\n// TODO b\n// TODO c\n")
            # Slack-Befehl -> Task (echtes grep) -> Ergebnis posten
            r = c.request("integrations.slack_command", {
                "command": "/cs count-todos todo-api",
                "exec": "grep -c TODO app.js",
                "response_url": response_url,
                "cwd": str(proj)})
            assert r["exit"] == 0 and r["output"] == "3", r
            assert r["posted_status"] == 200
            # der Slack-Channel (Mock) hat die Ergebnis-Nachricht empfangen
            for _ in range(20):
                if received:
                    break
                __import__("time").sleep(0.05)
            assert received, "Slack-Mock empfing keine Nachricht"
            msg = received[0]
            assert msg.get("response_type") == "in_channel"
            assert "count-todos" in msg["text"] and "3" in msg["text"]
            record("F358", "pass", ev("F358", "slack-bot.json",
                   {"command_result": r, "slack_message": msg}),
                   "Slack-Befehl löste Task aus (grep->3), Ergebnis als in_channel-Nachricht gepostet")
        except Exception as e:
            record("F358", "fail", note=str(e))
        c.close()

    server.shutdown()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
