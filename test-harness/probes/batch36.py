#!/usr/bin/env python3
"""Verifikation Build-Batch 36 (echter Core, Stub-`claude`, echtes curl/HTTP, kein Mock):

  F261  Slack-Notification wenn Agent fertig: ein Stop-/Post-Run-Hook POSTet bei Lauf-Ende eine
        Nachricht an die Webhook-URL; der Webhook antwortet mit HTTP 200, das im Hook-Log steht.

Der Slack-Webhook wird durch einen lokalen HTTP-Server ersetzt, der (wie Slack) 200 zurückgibt —
dasselbe legitime Lokal-Substitut-Muster wie bei den npm-/Scan-Tests. Verifiziert wird der echte
Mechanismus Agent-Stop -> Hook -> HTTP-POST -> 200.
"""
from __future__ import annotations
import json, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
STUB = ROOT / "test-harness" / "lib" / "stub_claude.sh"
results: dict[str, dict] = {}
received: list[dict] = []


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


class Webhook(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode("utf-8", "replace")
        try:
            received.append(json.loads(body))
        except Exception:
            received.append({"raw": body})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a):
        pass


def main():
    server = HTTPServer(("127.0.0.1", 0), Webhook)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b36.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": str(STUB)}) as ctx:
        c = P.Client(ctx["sock"], timeout=15)
        try:
            url = f"http://127.0.0.1:{port}/services/SLACK/HOOK"
            # Stop-/Post-Run-Hook: POSTet die Slack-Nachricht, gibt den HTTP-Code aus.
            hook = (f"curl -s -o /dev/null -w '%{{http_code}}' -X POST "
                    f"-H 'Content-type: application/json' "
                    f"-d '{{\"text\":\"✅ Agent für todo-api fertig\"}}' {url}")
            rid = str(uuid.uuid4())
            c.sock.sendall(P.encode_frame({"id": rid, "kind": "request", "method": "session.start",
                "payload": {"prompt": "Schließe die todo-api-Aufgabe ab", "cwd": str(ROOT),
                            "binary": str(STUB), "post_run_hook": hook}}))
            hook_event = None
            deadline = time.time() + 15
            while time.time() < deadline:
                f = c._read_frame()
                if f.get("id") == rid and f.get("kind") != "event":
                    continue
                if f.get("method") == "session.event" or f.get("kind") == "event":
                    evd = (f.get("payload") or {}).get("event") or {}
                    if evd.get("kind") == "post_run_hook":
                        hook_event = evd
                    if evd.get("kind") == "done":
                        break
            # warte kurz, falls der POST minimal nachläuft
            for _ in range(20):
                if received:
                    break
                time.sleep(0.05)
            assert hook_event is not None, "kein post_run_hook-Event"
            assert hook_event["stdout"].strip() == "200", hook_event  # HTTP 200 im Log
            assert received and received[0].get("text", "").startswith("✅ Agent"), received
            record("F261", "pass", ev("F261", "slack-webhook.json",
                   {"hook_event": hook_event, "webhook_received": received[0],
                    "http_status_in_log": hook_event["stdout"].strip()}),
                   "Stop-Hook POSTete die Slack-Nachricht an den Webhook; HTTP 200 im Hook-Log")
        except Exception as e:
            record("F261", "fail", note=str(e))
        c.close()

    server.shutdown()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
