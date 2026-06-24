#!/usr/bin/env python3
"""Verifikation Build-Batch 70 (echter Core, ECHTER claude, echtes git/curl, kein Mock der PR-Logik):

  F268  PR-Erstellung mit AI-Titel/Beschreibung: aus den echten Commits + dem echten Diff eines
        Feature-Branches generiert der echte claude einen PR-Titel + Markdown-Beschreibung; der Core
        legt damit per HTTP-POST einen echten Pull Request an (Nummer + html_url zurück).

Die GitHub-REST-API (/repos/:repo/pulls) wird durch einen lokalen Server mit PR-Shape ersetzt
(gleiches Lokal-Substitut-Muster wie beim Issues-Sync, batch46) — gh-Auth bleibt extern. Getestet
werden die echte Diff-Erhebung, die echte AI-Textgenerierung und der echte HTTP-POST des Core.
"""
from __future__ import annotations
import json, os, re, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
CLAUDE = os.path.expanduser("~/.local/bin/claude")
results: dict[str, dict] = {}
CREATED: list[dict] = []
NEXT = {"n": 42}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


class GitHubMock(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode() if n else "{}"
        if re.match(r"^/repos/.+/pulls$", self.path):
            payload = json.loads(body or "{}")
            num = NEXT["n"]; NEXT["n"] += 1
            pr = {"number": num,
                  "html_url": f"https://github.com/acme/todo-api/pull/{num}",
                  "title": payload.get("title", ""), "body": payload.get("body", ""),
                  "head": payload.get("head"), "base": payload.get("base"), "state": "open"}
            CREATED.append(pr)
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(pr).encode())
            return
        self.send_response(404); self.end_headers(); self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "B", "GIT_AUTHOR_EMAIL": "b@b",
                        "GIT_COMMITTER_NAME": "B", "GIT_COMMITTER_EMAIL": "b@b"})


def make_repo() -> Path:
    repo = Path(tempfile.mkdtemp(prefix="cs-f268-"))
    git(repo, "init", "-q", "-b", "main")
    (repo / "index.js").write_text(
        "const express = require('express');\nconst app = express();\n"
        "let todos = [{id:1,title:'x'}];\n"
        "app.get('/todos', (req,res)=>res.json(todos));\napp.listen(3000);\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "initial todo-api")
    # Feature-Branch mit echter Änderung: DELETE-Endpoint
    git(repo, "checkout", "-q", "-b", "feat/delete-todo")
    (repo / "index.js").write_text(
        "const express = require('express');\nconst app = express();\n"
        "let todos = [{id:1,title:'x'}];\n"
        "app.get('/todos', (req,res)=>res.json(todos));\n"
        "app.delete('/todos/:id', (req,res)=>{\n"
        "  todos = todos.filter(t => t.id !== Number(req.params.id));\n"
        "  res.status(204).end();\n});\n"
        "app.listen(3000);\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "add DELETE /todos/:id endpoint")
    return repo


def main():
    server = HTTPServer(("127.0.0.1", 0), GitHubMock)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    repo = make_repo()

    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/b70.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": CLAUDE}) as ctx:
        c = P.Client(ctx["sock"], timeout=420)
        try:
            r = c.request("deployment.create_pr",
                          {"cwd": str(repo), "repo": "acme/todo-api", "base": "main", "api_base": base})
            assert r.get("number"), f"keine PR-Nummer: {r}"
            assert r["html_url"], "keine html_url"
            title = (r.get("title") or "").lower()
            body = r.get("body") or ""
            assert title.strip(), "leerer PR-Titel"
            assert body.strip(), "leere PR-Beschreibung"
            # AI-Titel/Body aufgabenbezogen?
            blob = (title + " " + body.lower())
            assert any(k in blob for k in ("delete", "endpoint", "todo", "route", "remove")), \
                f"PR-Text nicht aufgabenbezogen: {title!r}"
            assert r["head"] == "feat/delete-todo" and r["base"] == "main"
            # Substitut hat den echten POST mit genau diesem Titel/Body empfangen
            assert CREATED and CREATED[-1]["title"] == r["title"] and CREATED[-1]["body"] == body, \
                "Substitut empfing den PR nicht / Abweichung"
            record("F268", "pass", ev("F268", "create-pr.json",
                   {"pr_number": r["number"], "html_url": r["html_url"], "title": r["title"],
                    "body_excerpt": body[:400], "head": r["head"], "base": r["base"],
                    "commit_count": r.get("commit_count"), "server_recorded": CREATED[-1]}),
                   f"AI-PR #{r['number']} erstellt: Titel {r['title']!r}, echter POST an /pulls")
        except Exception as e:
            record("F268", "fail", note=str(e))
        c.close()

    server.shutdown()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
