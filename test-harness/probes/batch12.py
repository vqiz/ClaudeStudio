#!/usr/bin/env python3
"""Verifikation Build-Batch 12: Task-Analyzer + Hook-Firing gegen den echten Core.

Neu (router.rs): security.code_scan (OWASP), changelog.generate, release_notes.generate,
readme.generate, hooks.types, hooks.run (firing/dry-run/block). Plus F205=codeq.dead_code,
F208=deploy.checklist (Wiederverwendung). Echte Dateien/Repos, echter Core. Kein Mock.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}
GENV = {**os.environ, "GIT_AUTHOR_NAME": "B12", "GIT_AUTHOR_EMAIL": "b12@cs.test",
        "GIT_COMMITTER_NAME": "B12", "GIT_COMMITTER_EMAIL": "b12@cs.test"}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=GENV)


def main():
    proj = Path(tempfile.mkdtemp(prefix="cs-b12-proj-"))
    (proj / "package.json").write_text('{"name":"demo"}\n')
    (proj / "api.ts").write_text(
        "export function getUser(req) {\n"
        "  const query = `SELECT * FROM users WHERE id=${req.params.id}`\n"
        "  return db.run(query)\n"
        "}\n"
        "export function unusedThing() { return 1 }\n"
        "export const used = getUser\n"
    )
    repo = Path(tempfile.mkdtemp(prefix="cs-b12-repo-"))
    git(repo, "init", "-q", "-b", "main")
    for fn, msg in [("a", "feat: add login"), ("b", "fix: null pointer in parser"), ("c", "feat: dark mode"), ("d", "chore: bump deps")]:
        (repo / fn).write_text(msg)
        git(repo, "add", "-A"); git(repo, "commit", "-qm", msg)

    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b12.log")) as ctx:
        c = P.Client(ctx["sock"])

        # F203 OWASP security code scan
        try:
            r = c.request("security.code_scan", {"cwd": str(proj)})
            sqli = next((f for f in r["findings"] if f["kind"] == "SQL-Injection"), None)
            assert sqli and sqli["file"].endswith("api.ts") and sqli["line"] == 2
            record("F203", "pass", ev("F203", "owasp.json", r), f"SQL-Injection in api.ts:{sqli['line']} gefunden")
        except Exception as e:
            record("F203", "fail", note=str(e))

        # F205 dead-code detector task
        try:
            r = c.request("codeq.dead_code", {"cwd": str(proj)})
            assert any(d["name"] == "unusedThing" for d in r["dead_exports"])
            record("F205", "pass", ev("F205", "deadcode.json", r), "ungenutzten Code mit Fundstelle erkannt")
        except Exception as e:
            record("F205", "fail", note=str(e))

        # F206 README generator
        try:
            r = c.request("readme.generate", {"cwd": str(proj)})
            assert "Node.js" in r["stack"] and "# " in r["readme"] and "api.ts" in r["readme"]
            record("F206", "pass", ev("F206", "readme.md", r["readme"]), "projektspezifische README erzeugt")
        except Exception as e:
            record("F206", "fail", note=str(e))

        # F207 changelog generator
        try:
            r = c.request("changelog.generate", {"cwd": str(repo)})
            assert "## Features" in r["changelog"] and "## Fixes" in r["changelog"] and "dark mode" in r["changelog"]
            record("F207", "pass", ev("F207", "changelog.md", r["changelog"]), "Changelog aus Git-Historie gruppiert")
        except Exception as e:
            record("F207", "fail", note=str(e))

        # F208 pre-deploy checklist ampel status
        try:
            red = c.request("deploy.checklist", {"checks": [{"name": "tests", "pass": False}]})
            green = c.request("deploy.checklist", {"checks": [{"name": "tests", "pass": True}]})
            assert red["status"] == "red" and green["status"] == "green"
            record("F208", "pass", ev("F208", "checklist.json", {"red": red, "green": green}), "Ampel-Status Deploy-Bereitschaft")
        except Exception as e:
            record("F208", "fail", note=str(e))

        # F209 release notes generator
        try:
            r = c.request("release_notes.generate", {"cwd": str(repo)})
            assert r["features"] >= 2 and r["fixes"] >= 1 and "Neue Funktionen" in r["release_notes"]
            assert "Add login" in r["release_notes"]  # friendly-capitalized, prefix stripped
            record("F209", "pass", ev("F209", "release.md", r["release_notes"]), "nutzerfreundliche Release-Notes")
        except Exception as e:
            record("F209", "fail", note=str(e))

        # F256 hook types
        try:
            t = c.request("hooks.types", {})["types"]
            names = {x["name"] for x in t}
            assert {"PreToolUse", "PostToolUse", "Stop", "WorktreeRemove"} <= names
            assert next(x for x in t if x["name"] == "PreToolUse")["can_block"] is True
            record("F256", "pass", ev("F256", "types.json", t), "7 Hook-Typen (PreToolUse blockierfähig)")
        except Exception as e:
            record("F256", "fail", note=str(e))

        # F258 auto-format PostToolUse hook actually runs on the edited file
        try:
            hp = Path(tempfile.mkdtemp(prefix="cs-b12-hooks-"))
            target = hp / "code.js"
            target.write_text("const x = 1\n")
            # a real formatting-style command operating on $CS_FILE (prettier is the user's choice of cmd)
            c.request("hooks.add", {"cwd": str(hp), "event": "PostToolUse", "matcher": "Edit|Write",
                                    "command": 'tr a-z A-Z < "$CS_FILE" > "$CS_FILE.t" && mv "$CS_FILE.t" "$CS_FILE"'})
            r = c.request("hooks.run", {"cwd": str(hp), "event": "PostToolUse", "tool": "Write", "file": str(target)})
            assert target.read_text() == "CONST X = 1\n" and r["fired"][0]["exit"] == 0
            record("F258", "pass", ev("F258", "autoformat.json", {"after": target.read_text(), "run": r}),
                   "PostToolUse-Hook führt Befehl auf der editierten Datei aus")
            globals()["_HP"] = hp
        except Exception as e:
            record("F258", "fail", note=str(e))

        # F260 PreToolUse hook blocks dangerous bash
        try:
            hp = globals().get("_HP") or Path(tempfile.mkdtemp(prefix="cs-b12-hooks2-"))
            c.request("hooks.add", {"cwd": str(hp), "event": "PreToolUse", "matcher": "Bash",
                                    "command": 'case "$CS_TOOL_INPUT" in *"rm -rf"*) echo blocked >&2; exit 2;; esac'})
            danger = c.request("hooks.run", {"cwd": str(hp), "event": "PreToolUse", "tool": "Bash", "input": "rm -rf /etc"})
            safe = c.request("hooks.run", {"cwd": str(hp), "event": "PreToolUse", "tool": "Bash", "input": "ls -la"})
            assert danger["blocked"] is True and safe["blocked"] is False
            record("F260", "pass", ev("F260", "block.json", {"danger": danger, "safe": safe}),
                   "PreToolUse-Hook fängt 'rm -rf' ab")
        except Exception as e:
            record("F260", "fail", note=str(e))

        # F265 dry-run shows which hooks would fire without executing
        try:
            hp = globals().get("_HP")
            before = (hp / "code.js").read_text()
            r = c.request("hooks.run", {"cwd": str(hp), "event": "PostToolUse", "tool": "Write",
                                        "file": str(hp / "code.js"), "dry_run": True})
            after = (hp / "code.js").read_text()
            assert r["dry_run"] is True and r["fired"] and r["fired"][0].get("would_fire") is True
            assert before == after  # nothing executed
            record("F265", "pass", ev("F265", "dryrun.json", r), "Dry-Run zeigt feuernde Hooks ohne Ausführung")
        except Exception as e:
            record("F265", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
