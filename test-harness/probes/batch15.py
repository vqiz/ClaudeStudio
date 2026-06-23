#!/usr/bin/env python3
"""Verifikation Build-Batch 15: Project-Hub-Registry + File-Explorer-Extras gegen den Core.

Neu (router.rs): projects.create/list/get/detect_stack/import/scaffold/rename/remove/
online_status; file.git_colors/diff/find/to_asset. Echte Verzeichnisse/Repos. Kein Mock.
(UI-Klick F043 + reine Darstellungs-Features bleiben separat blocked.)
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}
HOME = Path(tempfile.mkdtemp(prefix="cs-b15-home-"))
GENV = {**os.environ, "GIT_AUTHOR_NAME": "B15", "GIT_AUTHOR_EMAIL": "b15@cs.test",
        "GIT_COMMITTER_NAME": "B15", "GIT_COMMITTER_EMAIL": "b15@cs.test"}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=GENV)


def main():
    node_dir = Path(tempfile.mkdtemp(prefix="cs-b15-node-")); (node_dir / "package.json").write_text('{"name":"x"}')
    rust_dir = Path(tempfile.mkdtemp(prefix="cs-b15-rust-")); (rust_dir / "Cargo.toml").write_text("[package]\n")
    with P.running_core(home=HOME, library_dir=ROOT, log_path=Path("/tmp/b15.log")) as ctx:
        c = P.Client(ctx["sock"])

        # F035: wizard step 1 — create project with git init (fresh repo)
        try:
            wiz = Path(tempfile.mkdtemp(prefix="cs-b15-wiz-"))
            r = c.request("projects.create", {"name": "Wizardly", "path": str(wiz), "git_init": True})
            assert r["git"] is True and (wiz / ".git").exists()
            record("F035", "pass", ev("F035", "wizard1.json", r), "Git-Repo via Wizard angelegt (git init)")
        except Exception as e:
            record("F035", "fail", note=str(e))

        # A committed project for the card section (real repo with a branch).
        proj = Path(tempfile.mkdtemp(prefix="cs-b15-abrevia-"))
        git(proj, "init", "-q", "-b", "main")
        (proj / "package.json").write_text('{"name":"abrevia"}')
        git(proj, "add", "-A"); git(proj, "commit", "-qm", "init")
        c.request("projects.create", {"name": "Abrevia", "path": str(proj)})

        # F036 / F038: stack auto-detection (Node / Rust / Python)
        try:
            node = c.request("projects.detect_stack", {"path": str(node_dir)})["stack"]
            rust = c.request("projects.detect_stack", {"path": str(rust_dir)})["stack"]
            assert node == ["Node.js"] and rust == ["Rust"]
            record("F036", "pass", ev("F036", "stack.json", {"node": node, "rust": rust}), "Wizard Schritt 2: Stack erkannt")
            record("F038", "pass", ev("F038", "stack-engine.json", {"node": node, "rust": rust}), "Stack-Engine Node/Rust korrekt")
        except Exception as e:
            for fid in ["F036", "F038"]:
                if fid not in results: record(fid, "fail", note=str(e))

        # F037: wizard step 3+4 — scaffold CLAUDE.md template + default agent
        try:
            r = c.request("projects.scaffold", {"path": str(node_dir), "template": "next"})
            assert Path(r["claude_md"]).exists() and (node_dir / ".claude/agents/default.json").exists()
            record("F037", "pass", ev("F037", "scaffold.json", r), "CLAUDE.md-Template + Standard-Agent geschrieben")
        except Exception as e:
            record("F037", "fail", note=str(e))

        # F039: import a project with existing .claude/
        try:
            imp = Path(tempfile.mkdtemp(prefix="cs-b15-imp-"))
            (imp / ".claude/commands").mkdir(parents=True)
            (imp / ".claude/commands/foo.md").write_text("# foo")
            (imp / ".claude/settings.json").write_text("{}")
            r = c.request("projects.import", {"path": str(imp), "name": "Imported"})
            assert r["skills_found"] == 1 and r["settings_found"] is True
            record("F039", "pass", ev("F039", "import.json", r), "Projekt mit .claude/ importiert (Skills/Settings erkannt)")
        except Exception as e:
            record("F039", "fail", note=str(e))

        # F040: online-status ping
        try:
            on = c.request("projects.online_status", {"url": "http://localhost:6333/healthz"})
            off = c.request("projects.online_status", {"url": "http://localhost:6399/x"})
            assert on["online"] is True and off["online"] is False
            record("F040", "pass", ev("F040", "online.json", {"on": on, "off": off}), "Online-Status-Ping (Qdrant up vs toter Port)")
        except Exception as e:
            record("F040", "fail", note=str(e))

        # F041 / F042: rename + remove persistent in registry
        try:
            c.request("projects.rename", {"name": "Imported", "new_name": "Renamed"})
            after = {p["name"] for p in c.request("projects.list", {})["projects"]}
            assert "Renamed" in after and "Imported" not in after
            record("F041", "pass", ev("F041", "rename.json", sorted(after)), "Projekt umbenannt (persistent)")
            c.request("projects.remove", {"name": "Renamed"})
            after2 = {p["name"] for p in c.request("projects.list", {})["projects"]}
            assert "Renamed" not in after2
            record("F042", "pass", ev("F042", "remove.json", sorted(after2)), "Projekt aus Registry entfernt")
        except Exception as e:
            for fid in ["F041", "F042"]:
                if fid not in results: record(fid, "fail", note=str(e))

        # F045 / F034 / F044: project card with branch, name, stack, cost today
        try:
            sid = c.request("session.create", {"title": "work", "cwd": str(ctx['home'])})["id"]
            c.request("session.record_usage", {"session_id": sid, "project": "Abrevia", "cost_usd": 1.25, "model": "sonnet"})
            cards = c.request("projects.list", {})["projects"]
            abrevia = next(p for p in cards if p["name"] == "Abrevia")
            assert abrevia["branch"] == "main"
            record("F045", "pass", ev("F045", "branch.json", abrevia), "Card zeigt aktuellen Git-Branch")
            assert "name" in abrevia and "stack" in abrevia and "cost_today_usd" in abrevia
            record("F034", "pass", ev("F034", "card.json", abrevia), "Project-Card mit Name/Stack/Branch/Kosten/Status")
            assert abs(abrevia["cost_today_usd"] - 1.25) < 1e-9
            record("F044", "pass", ev("F044", "cost-today.json", abrevia), "Card zeigt aggregierte Tageskosten")
        except Exception as e:
            for fid in ["F045", "F034", "F044"]:
                if fid not in results: record(fid, "fail", note=str(e))

        # F058 / F059 / F060 / F056: file-explorer extras
        try:
            repo = Path(tempfile.mkdtemp(prefix="cs-b15-repo-"))
            git(repo, "init", "-q", "-b", "main")
            (repo / "keep.txt").write_text("v1\n"); (repo / "del.txt").write_text("bye\n")
            git(repo, "add", "-A"); git(repo, "commit", "-qm", "init")
            (repo / "keep.txt").write_text("v2 changed\n")      # modified -> orange
            (repo / "fresh.txt").write_text("new\n")            # untracked -> green
            (repo / "del.txt").unlink()                          # deleted -> red
            colors = {f["path"]: f["color"] for f in c.request("file.git_colors", {"cwd": str(repo)})["files"]}
            assert colors.get("keep.txt") == "orange" and colors.get("fresh.txt") == "green" and colors.get("del.txt") == "red"
            record("F058", "pass", ev("F058", "git-colors.json", colors), "Git-Status-Farben (orange/grün/rot)")

            diff = c.request("file.diff", {"cwd": str(repo), "path": "keep.txt"})
            assert diff["added"] >= 1 and diff["removed"] >= 1 and "v2 changed" in diff["diff"]
            record("F059", "pass", ev("F059", "file-diff.json", diff), "Diff-Indikator pro Datei zur committeten Version")
        except Exception as e:
            for fid in ["F058", "F059"]:
                if fid not in results: record(fid, "fail", note=str(e))

        try:
            findr = Path(tempfile.mkdtemp(prefix="cs-b15-find-"))
            (findr / "InvoiceService.ts").write_text("x")
            (findr / "userRepository.ts").write_text("x")
            r = c.request("file.find", {"cwd": str(findr), "query": "invsvc"})  # fuzzy subseq of invoiceservice
            top = r["matches"][0]["path"] if r["matches"] else ""
            assert top.endswith("InvoiceService.ts"), f"matches={r['matches']}"
            record("F060", "pass", ev("F060", "fuzzy.json", r), "Fuzzy-Dateiname-Suche findet Treffer")
        except Exception as e:
            record("F060", "fail", note=str(e))

        try:
            r = c.request("file.to_asset", {"path": "/abrevia/public/logo.svg", "cwd": str(node_dir)})
            assert r["node_id"] and r["label"] == "logo.svg"
            found = c.request("graph.search", {"query": "logo.svg"})["nodes"]
            assert any(n["label"] == "logo.svg" for n in found)
            record("F056", "pass", ev("F056", "to-asset.json", {"created": r, "in_graph": found}),
                   "Datei als Asset-Node im Brain-Graph angelegt")
        except Exception as e:
            record("F056", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
