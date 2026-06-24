#!/usr/bin/env python3
"""Verifikation LLM-Batch 7 (echter Core, ECHTER claude, echtes git-worktree+merge):

  F126  Kompletter Team-Flow: der echte claude implementiert 'Token-Auth zu todo-api hinzufügen' in
        einem Worktree; die Änderungen werden committet und nach main gemergt; der Main-Branch enthält
        danach echten Auth-Code (git log + Code-Inhalt geprüft).
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
CLAUDE = os.path.expanduser("~/.local/bin/claude")
results: dict[str, dict] = {}
GENV = {**os.environ, "GIT_AUTHOR_NAME": "B126", "GIT_AUTHOR_EMAIL": "b126@cs.test",
        "GIT_COMMITTER_NAME": "B126", "GIT_COMMITTER_EMAIL": "b126@cs.test"}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=GENV)


def main():
    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/llm7.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": CLAUDE}) as ctx:
        c = P.Client(ctx["sock"], timeout=600)
        try:
            repo = Path(tempfile.mkdtemp(prefix="cs-f126-"))
            git(repo, "init", "-q", "-b", "main")
            (repo / "package.json").write_text('{"name":"todo-api","type":"commonjs","version":"1.0.0"}')
            (repo / "index.js").write_text(
                "const express = require('express');\n"
                "const app = express();\n"
                "app.get('/todos', (req, res) => { res.json([{id:1,title:'demo'}]); });\n"
                "app.listen(3000);\n"
                "module.exports = app;\n")
            git(repo, "add", "-A"); git(repo, "commit", "-qm", "init todo-api")
            commits_before = git(repo, "rev-list", "--count", "HEAD").stdout.strip()

            r = c.request("teams.run_flow", {"cwd": str(repo),
                "task": "Füge eine Token-basierte Authentifizierungs-Middleware hinzu (prüft den "
                        "'Authorization: Bearer <token>'-Header) und schütze die bestehende /todos-Route damit."})
            assert r["merged"] is True, f"Merge fehlgeschlagen: {r.get('merge_output')}"
            assert r["files_changed"], "keine Dateien gemergt"
            assert "merge team work" in r["main_log"], f"kein Merge-Commit im Log: {r['main_log']}"

            commits_after = git(repo, "rev-list", "--count", "HEAD").stdout.strip()
            assert int(commits_after) > int(commits_before), "kein neuer Commit auf main"

            # main enthält echten Auth-Code
            main_blob = ""
            for f in repo.rglob("*.js"):
                if "node_modules" not in str(f):
                    main_blob += f.read_text(errors="replace").lower()
            assert any(k in main_blob for k in ("authorization", "bearer", "token", "auth")), \
                "kein Auth-Code im Main-Branch"
            record("F126", "pass", ev("F126", "team-flow.json",
                   {"merged": True, "files_changed": r["files_changed"],
                    "commits_before": commits_before, "commits_after": commits_after,
                    "main_log": r["main_log"]}),
                   f"Team implementierte Auth, gemergt nach main ({r['files_changed']}); Auth-Code vorhanden")
        except Exception as e:
            record("F126", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
