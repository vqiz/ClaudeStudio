#!/usr/bin/env python3
"""Verifikation Build-Batch 18: semantischer Knowledge-Store (teach/retrieve via echtem
embed_cli/MiniLM), CSS-Variable-Extraktor, Skill-Library-Git-Sync. Echter Core. Kein Mock.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
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


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b18.log")) as ctx:
        c = P.Client(ctx["sock"])

        # F180 Teach-Claude + retrieval next session
        try:
            c.request("knowledge.teach", {"text": "Stripe payment integration handles all invoice billing", "collection": "knowledge", "source": "pdf-drop"})
            c.request("knowledge.teach", {"text": "Chocolate cake recipe with two eggs and flour", "collection": "knowledge", "source": "noise"})
            r = c.request("knowledge.search", {"query": "Zahlungsanbieter Rechnung", "collection": "knowledge"})
            top = r["hits"][0]
            assert "Stripe payment" in top["text"] and top["score"] > 0.5
            record("F180", "pass", ev("F180", "teach.json", r),
                   f"gedroppter Inhalt im Retrieval gefunden (Score {top['score']:.3f})")
        except Exception as e:
            record("F180", "fail", note=str(e))

        # F174 retrieval pipeline: prompt -> embed -> query -> top-K ranked
        try:
            r = c.request("knowledge.search", {"query": "How did we integrate payments?", "collection": "knowledge", "top_k": 2})
            assert len(r["hits"]) <= 2 and r["hits"][0]["score"] >= r["hits"][-1]["score"]
            assert "Stripe payment" in r["hits"][0]["text"]
            record("F174", "pass", ev("F174", "pipeline.json", r), "Retrieval-Pipeline Prompt->Embedding->Top-K (rangiert)")
        except Exception as e:
            record("F174", "fail", note=str(e))

        # F177 knowledge collection from a CLAUDE.md rule -> findable score>0.7
        try:
            rule = "Regel: Immer Logging in catch-Bloecken hinzufuegen und Fehler nie verschlucken"
            c.request("knowledge.teach", {"text": rule, "collection": "knowledge", "source": "CLAUDE.md"})
            r = c.request("knowledge.search", {"query": "Logging in catch-Bloecken Regel Fehler", "collection": "knowledge"})
            hit = next((h for h in r["hits"] if "catch-Bloecken" in h["text"]), None)
            assert hit and hit["score"] > 0.7
            record("F177", "pass", ev("F177", "knowledge-rule.json", hit), f"CLAUDE.md-Regel in knowledge auffindbar (Score {hit['score']:.3f}>0.7)")
        except Exception as e:
            record("F177", "fail", note=str(e))

        # F179 errors collection: error+solution -> findable score>0.7
        try:
            err = "Error: NullPointerException im Parser. Loesung: Null-Check vor dem Zugriff einfuegen"
            c.request("knowledge.teach", {"text": err, "collection": "errors", "source": "session-hook"})
            r = c.request("knowledge.search", {"query": "NullPointerException Parser Fehler Loesung Null-Check", "collection": "errors"})
            hit = r["hits"][0]
            assert "NullPointer" in hit["text"] and hit["score"] > 0.7 and hit["collection"] == "errors"
            record("F179", "pass", ev("F179", "errors.json", hit), f"Fehler+Loesung in errors auffindbar (Score {hit['score']:.3f}>0.7)")
        except Exception as e:
            record("F179", "fail", note=str(e))

        # F339 CSS variable extractor
        try:
            css = ".a{color:#1A73E8}.b{border:1px solid #1A73E8}.c{background:#1A73E8}.d{color:#EA4335}.e{color:#EA4335}"
            r = c.request("css.extract", {"css": css})
            assert r["extracted"] >= 2 and "var(--color-0)" in r["transformed"]
            assert any(v == "#1A73E8" for v in r["variables"].values())
            record("F339", "pass", ev("F339", "css.json", r), "wiederholte Hex-Werte durch :root-Variablen ersetzt")
        except Exception as e:
            record("F339", "fail", note=str(e))

        # F351 skill library git sync (push to bare remote, verify in a clone)
        try:
            bare = Path(tempfile.mkdtemp(prefix="cs-b18-bare-")) / "skills.git"
            subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
            skills = Path(tempfile.mkdtemp(prefix="cs-b18-skills-"))
            (skills / "deploy.md").write_text("# /deploy\nDeploy the app.\n")
            r = c.request("library.git_sync", {"dir": str(skills), "remote": str(bare)})
            assert r["ok"] is True
            clone = Path(tempfile.mkdtemp(prefix="cs-b18-clone-")) / "c"
            subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
            assert (clone / "deploy.md").exists() and "/deploy" in (clone / "deploy.md").read_text()
            record("F351", "pass", ev("F351", "git-sync.json", {"push": r, "in_clone": True}),
                   "Skill via Git-Sync gepusht und im zweiten Clone identisch vorhanden")
        except Exception as e:
            record("F351", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
