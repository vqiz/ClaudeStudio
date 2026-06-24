#!/usr/bin/env python3
"""Verifikation LLM-Batch 11 (echter Core, ECHTER claude + Playwright-MCP, echtes chromium):

  F348  Computer-Use / Browser-Agent: der echte claude erhält den Playwright-MCP-Server und füllt
        autonom ein Formular auf einer localhost-Seite aus und schickt es ab; der Server empfängt die
        echten Formulardaten (Name + Email).
"""
from __future__ import annotations
import json, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
CLAUDE = __import__("os").path.expanduser("~/.local/bin/claude")
results: dict[str, dict] = {}
submissions: list[dict] = []

FORM = (
    "<!doctype html><html><head><meta charset='utf-8'><title>Anmeldung</title></head><body>"
    "<h1>Anmeldung</h1>"
    "<form action='/submit' method='POST'>"
    "<p>Name: <input type='text' name='name' id='name'></p>"
    "<p>Email: <input type='email' name='email' id='email'></p>"
    "<button type='submit' id='submit'>Absenden</button>"
    "</form></body></html>"
)


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


class FormServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
        self.wfile.write(FORM.encode())

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode("utf-8", "replace")
        submissions.append({k: v[0] for k, v in urllib.parse.parse_qs(body).items()})
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
        self.wfile.write("<!doctype html><html><body><h1 id='ok'>Danke, abgeschickt!</h1></body></html>".encode())

    def log_message(self, *a):
        pass


def main():
    server = HTTPServer(("127.0.0.1", 0), FormServer)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"

    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/llm11.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": CLAUDE}) as ctx:
        c = P.Client(ctx["sock"], timeout=600)
        try:
            task = (f"Navigiere mit dem Browser zu {url} . Dort ist ein Anmeldeformular. Trage in das "
                    f"Namensfeld 'Max Mustermann' ein und in das E-Mail-Feld 'max@example.com', dann "
                    f"klicke den 'Absenden'-Button. Bestätige danach, dass die Seite 'Danke, abgeschickt!' zeigt.")
            r = c.request("agents.browser_task", {"task": task, "cwd": str(Path(tempfile := __import__('tempfile').mkdtemp(prefix='cs-f348-')))})
            for _ in range(20):
                if submissions:
                    break
                time.sleep(0.2)
            assert submissions, f"Server empfing keine Formulardaten (agent log: {r.get('agent_log_tail','')[:200]})"
            sub = submissions[-1]
            assert sub.get("name") == "Max Mustermann", f"falscher Name: {sub}"
            assert sub.get("email") == "max@example.com", f"falsche Email: {sub}"
            record("F348", "pass", ev("F348", "browser-agent.json",
                   {"submission": sub, "agent_log_tail": r.get("agent_log_tail", "")[:300]}),
                   "Browser-Agent füllte das Formular aus + sendete es ab (Server empfing Name+Email)")
        except Exception as e:
            record("F348", "fail", note=str(e))
        c.close()

    server.shutdown()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
