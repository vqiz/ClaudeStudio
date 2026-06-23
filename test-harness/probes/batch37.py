#!/usr/bin/env python3
"""Verifikation Build-Batch 37 (echter Core, echtes git, kein Mock):

  F052  Status-Indikatoren je Datei: bearbeitet (git), geschützt (is_protected_path) und
        Brain-Graph-Asset (Asset-Knoten) ergeben je Datei die richtigen Symbole.
  F063  Cross-Project-Modus: mehrere Projekt-Wurzeln erscheinen parallel mit ihren Einträgen.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}
GENV = {**os.environ, "GIT_AUTHOR_NAME": "B37", "GIT_AUTHOR_EMAIL": "b37@cs.test",
        "GIT_COMMITTER_NAME": "B37", "GIT_COMMITTER_EMAIL": "b37@cs.test"}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=GENV)


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b37.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=15)

        # F052 — Status-Indikatoren je Datei
        try:
            repo = Path(tempfile.mkdtemp(prefix="cs-b37-status-"))
            git(repo, "init", "-q", "-b", "main")
            (repo / "deploy.key").write_text("TOKEN=abc\n")     # geschützt
            (repo / "logo.svg").write_text("<svg/>\n")          # Brain-Asset
            (repo / "main.py").write_text("print(1)\n")         # wird bearbeitet
            git(repo, "add", "-A"); git(repo, "commit", "-qm", "init")
            (repo / "main.py").write_text("print(2)  # edit\n")  # echte Änderung
            # logo.svg als Brain-Graph-Asset markieren
            c.request("file.to_asset", {"path": str(repo / "logo.svg")})

            r = c.request("files.status_indicators",
                          {"cwd": str(repo), "files": ["deploy.key", "logo.svg", "main.py"]})
            by = {f["path"]: f for f in r["files"]}
            assert by["deploy.key"]["protected"] and "lock" in by["deploy.key"]["symbols"]
            assert by["logo.svg"]["brain_asset"] and "brain" in by["logo.svg"]["symbols"]
            assert by["main.py"]["edited"] and "pencil" in by["main.py"]["symbols"]
            # keine Kreuz-Kontamination
            assert not by["deploy.key"]["edited"] and not by["main.py"]["protected"]
            assert not by["logo.svg"]["edited"] and not by["main.py"]["brain_asset"]
            record("F052", "pass", ev("F052", "status-indicators.json", r),
                   "geschützt(lock)/Brain-Asset(brain)/bearbeitet(pencil) korrekt je Datei")
        except Exception as e:
            record("F052", "fail", note=str(e))

        # F063 — Cross-Project-Modus: zwei Wurzeln parallel
        try:
            base = Path(tempfile.mkdtemp(prefix="cs-b37-cross-"))
            todo = base / "todo-api"; (todo / "src").mkdir(parents=True)
            (todo / "package.json").write_text('{"name":"todo-api"}')
            (todo / "src" / "index.ts").write_text("export const x = 1\n")
            land = base / "landing-page"; land.mkdir()
            (land / "index.html").write_text("<html></html>\n")
            r = c.request("files.cross_project_tree", {"roots": [str(todo), str(land)]})
            names = {pj["name"] for pj in r["projects"]}
            assert r["count"] == 2 and names == {"todo-api", "landing-page"}
            by = {pj["name"]: pj for pj in r["projects"]}
            todo_entries = {e["name"] for e in by["todo-api"]["entries"]}
            land_entries = {e["name"] for e in by["landing-page"]["entries"]}
            assert "package.json" in todo_entries and "src" in todo_entries
            assert "index.html" in land_entries
            record("F063", "pass", ev("F063", "cross-project.json", r),
                   "beide Projektwurzeln todo-api + landing-page parallel mit Einträgen")
        except Exception as e:
            record("F063", "fail", note=str(e))

        c.close()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
