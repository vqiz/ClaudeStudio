#!/usr/bin/env python3
"""Verifikation Build-Batch 27 (echter Core, echtes npm, echtes embed_cli, kein Mock):

  F079  Global-CLAUDE.md-Editor speichert ~/.claude/CLAUDE.md auf Disk (Marker on disk).
  F086  AGENTS.md visueller Editor erzeugt YAML-Frontmatter (name/description/tools).
  F090  Erkenntnis-Vorschlag: extrahiert merkbaren Fakt ('… heißt prod-01') aus Transcript.
  F181  Wissensaufbau-Hook: extrahiert Entitäten, bettet sie in 'knowledge' ein, semantisch
        wieder auffindbar.
  F262  PostToolUse-Hook 'npm install' bei package.json-Edit → node_modules wird befüllt
        (offline via file:-Dependency).
"""
from __future__ import annotations
import json, sys, tempfile
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


def parse_frontmatter(text: str) -> dict:
    """Unabhängiger YAML-Frontmatter-Parser (key: value, [a, b]-Listen)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm = {}
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        if ":" in ln:
            k, v = ln.split(":", 1)
            k, v = k.strip(), v.strip()
            if v.startswith("[") and v.endswith("]"):
                v = [x.strip() for x in v[1:-1].split(",") if x.strip()]
            fm[k] = v
    return fm


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b27.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=90)
        home = Path(ctx["home"])

        # F079 Global-CLAUDE.md auf Disk speichern (Editor-Save-Pfad)
        try:
            global_md = home / ".claude" / "CLAUDE.md"
            existing = global_md.read_text() if global_md.exists() else "# Global\n"
            new_content = existing + "\nEDIT_MARKER_F079\n"
            r = c.request("claudemd.save", {"content": new_content})  # kein path → ~/.claude/CLAUDE.md
            on_disk = global_md.read_text()  # direkt vom Dateisystem lesen
            assert "EDIT_MARKER_F079" in on_disk
            assert Path(r["path"]) == global_md
            record("F079", "pass", ev("F079", "global-claudemd.json",
                   {"path": r["path"], "contains_marker": True, "tail": on_disk[-60:]}),
                   "Datei auf Disk enthält EDIT_MARKER_F079 nach Save")
        except Exception as e:
            record("F079", "fail", note=str(e))

        # F086 AGENTS.md mit YAML-Frontmatter aus Formularfeldern
        try:
            proj = Path(tempfile.mkdtemp(prefix="cs-b27-agents-"))
            c.request("agents.write_agents_md", {
                "cwd": str(proj), "name": "build-agent",
                "description": "Builds and verifies the project",
                "tools": ["Read", "Edit", "Bash"]})
            text = (proj / "AGENTS.md").read_text()
            fm = parse_frontmatter(text)  # unabhängig geparst
            assert fm.get("name") == "build-agent"
            assert isinstance(fm.get("tools"), list) and "Read" in fm["tools"] and "Bash" in fm["tools"]
            record("F086", "pass", ev("F086", "agents-md.json",
                   {"frontmatter": fm, "file": text}),
                   "geparster Frontmatter: name='build-agent', tools=[Read,Edit,Bash]")
        except Exception as e:
            record("F086", "fail", note=str(e))

        # F090 Erkenntnis-Vorschlag extrahiert den merkbaren Fakt
        try:
            transcript = ("Wir haben heute die Architektur besprochen. Mein Deploy-Server heißt prod-01. "
                          "Danach ging es um die Tests.")
            r = c.request("memory.suggest_insight", {"transcript": transcript})
            facts = [s["fact"] for s in r["suggestions"]]
            assert any("prod-01" in f for f in facts), facts
            # der prod-01-Fakt ist der bestbewertete Vorschlag
            assert "prod-01" in r["suggestions"][0]["fact"]
            record("F090", "pass", ev("F090", "insight.json", r),
                   "Top-Vorschlag enthält den Fakt 'Mein Deploy-Server heißt prod-01'")
        except Exception as e:
            record("F090", "fail", note=str(e))

        # F181 Entitäten extrahieren, einbetten, semantisch wiederfinden
        try:
            transcript = "Heute stellte Maria Schmidt das Projekt Zeppelin-Migration vor."
            r = c.request("knowledge.extract_entities", {"transcript": transcript})
            ents = r["entities"]
            assert "Maria Schmidt" in ents and any("Zeppelin" in e for e in ents)
            assert r["embedded"] >= 2
            hits = c.request("knowledge.search",
                             {"query": "Maria Schmidt", "collection": "knowledge", "top_k": 5})["hits"]
            top = hits[0]
            assert top["text"] == "Maria Schmidt" and top["score"] > 0.7
            record("F181", "pass", ev("F181", "entities.json",
                   {"entities": ents, "embedded": r["embedded"], "top_hit": top}),
                   f"Entität 'Maria Schmidt' eingebettet + semantisch gefunden (Score {top['score']:.3f})")
        except Exception as e:
            record("F181", "fail", note=str(e))

        # F262 npm-install-Hook bei package.json-Edit (offline file:-Dependency)
        try:
            proj = Path(tempfile.mkdtemp(prefix="cs-b27-npm-"))
            (proj / "mylib").mkdir()
            (proj / "mylib" / "package.json").write_text(
                json.dumps({"name": "mylib", "version": "1.0.0"}))
            (proj / "package.json").write_text(json.dumps(
                {"name": "app", "version": "1.0.0", "dependencies": {"mylib": "file:./mylib"}}))
            c.request("hooks.add", {"cwd": str(proj), "event": "PostToolUse", "matcher": "Edit",
                                    "command": 'case "$CS_FILE" in *package.json) '
                                               'npm install --no-audit --no-fund --prefer-offline --silent;; esac'})
            run = c.request("hooks.run", {"cwd": str(proj), "event": "PostToolUse",
                                          "tool": "Edit", "file": str(proj / "package.json")})
            installed = (proj / "node_modules" / "mylib").exists()
            assert run["fired"] and run["fired"][0]["exit"] == 0, run
            assert installed, "node_modules/mylib fehlt"
            record("F262", "pass", ev("F262", "npm-install-hook.json",
                   {"run": run, "node_modules_mylib": installed}),
                   "PostToolUse-Hook lief 'npm install' bei package.json-Edit, node_modules befüllt")
        except Exception as e:
            record("F262", "fail", note=str(e))

        c.close()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
