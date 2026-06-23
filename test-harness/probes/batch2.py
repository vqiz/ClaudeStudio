#!/usr/bin/env python3
"""Verifikation Build-Batch 2: echte Kontext-Assemblierung + Memory gegen den Core.

Neu im Core (cs-cli/router.rs): context.assemble (liest die ECHTEN 6 Ebenen-Dateien
von Disk, feste Reihenfolge, Pro-Ebene-Toggle), memory.get/set/append, claudemd.save
(Backup-vor-Speichern). Jeder Check schreibt echte Dateien und liest über den Core
zurück. Kein Mock.
"""
from __future__ import annotations
import json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}
HOME = Path(tempfile.mkdtemp(prefix="cs-b2-home-"))  # fixed home -> Persistenz testbar
GLOBAL_MARK = "GLOBAL-CLAUDEMD-MARKER-7421"
MEM_MARK = "CROSSPROJECT-MEMORY-MARKER-3380"
PROJ_MARK = "PERPROJECT-MEMORY-MARKER-9156"


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def core():
    return P.running_core(home=HOME, library_dir=ROOT, log_path=Path("/tmp/b2.log"))


def main():
    # ---- Seed real files via the core, then verify reads/assembly ----
    with core() as ctx:
        c = P.Client(ctx["sock"])

        # F083: claudemd.save backs up the previous version before overwriting.
        try:
            c.request("claudemd.save", {"content": "# OLD VERSION ALPHA\n"})
            r2 = c.request("claudemd.save", {"content": f"# {GLOBAL_MARK}\nNeuer Inhalt.\n"})
            backup = r2.get("backup")
            assert backup, "no backup path returned"
            old = Path(backup).read_text()
            cur = (HOME / ".claude/CLAUDE.md").read_text()
            assert "OLD VERSION ALPHA" in old, "backup does not hold previous version"
            assert GLOBAL_MARK in cur, "new content not written"
            record("F083", "pass", ev("F083", "backup.json",
                   {"backup_path": backup, "backup_holds_old": True, "current_has_new": True}),
                   "alte Version vor Speichern gesichert")
        except Exception as e:
            record("F083", "fail", note=str(e))

        # memory.set global + project (also covers F089 persist + F094 per-project file)
        c.request("memory.set", {"scope": "global", "content": f"# Memory\n{MEM_MARK} — Stripe ist der Zahlungsanbieter.\n"})
        c.request("memory.set", {"scope": "project", "project": "data-pipeline", "content": f"{PROJ_MARK} — Pipeline nutzt DuckDB.\n"})

        # F089: inline edit persists immediately to disk
        try:
            disk = (HOME / ".claudestudio/memory/global.md").read_text()
            got = c.request("memory.get", {"scope": "global"})
            assert MEM_MARK in disk and MEM_MARK in got["content"]
            record("F089", "pass", ev("F089", "memory-persist.json", {"disk_has": True, "get": got}),
                   "memory.set sofort auf Disk persistiert")
        except Exception as e:
            record("F089", "fail", note=str(e))

        # F094: per-project memory file written
        try:
            pf = HOME / ".claudestudio/memory/projects/data-pipeline.md"
            assert pf.exists() and PROJ_MARK in pf.read_text()
            record("F094", "pass", ev("F094", "per-project.json", {"path": str(pf), "has_marker": True}),
                   "per-Projekt-Memory-Datei angelegt")
        except Exception as e:
            record("F094", "fail", note=str(e))

        # F091: append a suggested insight -> shows up afterwards
        try:
            c.request("memory.append", {"scope": "global", "text": "Erkenntnis: Deploy-Target ist Coolify."})
            got = c.request("memory.get", {"scope": "global"})
            assert "Coolify" in got["content"] and MEM_MARK in got["content"]
            record("F091", "pass", ev("F091", "append.json", got), "Erkenntnis an global.md angehängt")
        except Exception as e:
            record("F091", "fail", note=str(e))

        # F076: six-layer assembly in fixed order, with real content
        try:
            a = c.request("context.assemble", {"cwd": str(ROOT), "project": "data-pipeline"})
            order = a["order"]
            expected = ["global_claude_md", "cross_project_memory", "project_claude_md",
                        "vector_retrieval", "active_definitions", "worktree_override"]
            assert order == expected, f"order mismatch: {order}"
            assert [l["layer"] for l in a["layers"]] == expected
            assert GLOBAL_MARK in a["assembled_text"]
            record("F076", "pass", ev("F076", "assemble-order.json",
                   {"order": order, "assembled_excerpt": a["assembled_text"][:400]}),
                   "6 Ebenen feste Reihenfolge mit echtem Inhalt")
        except Exception as e:
            record("F076", "fail", note=str(e))

        # F087: cross-project memory loaded as layer 2 content
        try:
            a = c.request("context.assemble", {"cwd": str(ROOT)})
            l2 = next(l for l in a["layers"] if l["layer"] == "cross_project_memory")
            assert MEM_MARK in l2["content"] and MEM_MARK in a["assembled_text"]
            record("F087", "pass", ev("F087", "layer2.json", l2), "cross-project memory als Ebene2 geladen")
        except Exception as e:
            record("F087", "fail", note=str(e))

        # F095: per-project memory loaded into context for that project
        try:
            a = c.request("context.assemble", {"cwd": str(ROOT), "project": "data-pipeline"})
            assert PROJ_MARK in a["assembled_text"]
            a_other = c.request("context.assemble", {"cwd": str(ROOT), "project": "todo-api"})
            assert PROJ_MARK not in a_other["assembled_text"]  # nur fürs richtige Projekt
            record("F095", "pass", ev("F095", "per-project-load.json",
                   {"data_pipeline_has_marker": True, "todo_api_excludes_marker": True}),
                   "per-Projekt-Memory nur im eigenen Projekt geladen")
        except Exception as e:
            record("F095", "fail", note=str(e))

        # F084: global CLAUDE.md loadable into a session context
        try:
            a = c.request("context.assemble", {})
            l1 = next(l for l in a["layers"] if l["layer"] == "global_claude_md")
            assert GLOBAL_MARK in l1["content"] and GLOBAL_MARK in a["assembled_text"]
            record("F084", "pass", ev("F084", "load-global.json", {"layer1_loaded": True}),
                   "globale CLAUDE.md in Session-Kontext geladen (Button=UI)")
        except Exception as e:
            record("F084", "fail", note=str(e))

        # F077: per-layer toggle removes a disabled layer from the assembly
        try:
            a = c.request("context.assemble", {"cwd": str(ROOT), "layers": {"cross_project_memory": False}})
            l2 = next(l for l in a["layers"] if l["layer"] == "cross_project_memory")
            assert l2["enabled"] is False and MEM_MARK not in a["assembled_text"]
            # other layers still present
            assert GLOBAL_MARK in a["assembled_text"]
            record("F077", "pass", ev("F077", "toggle.json",
                   {"cross_project_memory_enabled": False, "marker_absent": True, "global_present": True}),
                   "abgeschaltete Ebene fehlt im Zusammenbau")
        except Exception as e:
            record("F077", "fail", note=str(e))

        # F078: budget shows per-layer tokens + total against configured budget
        try:
            a = c.request("context.assemble", {"cwd": str(ROOT)})
            assert a["total_budget"] > 0
            assert all("tokens" in l and "granted_tokens" in l for l in a["layers"])
            assert a["granted_total"] <= a["total_budget"]
            record("F078", "pass", ev("F078", "budget.json",
                   {"total_budget": a["total_budget"], "granted_total": a["granted_total"],
                    "per_layer": [(l["layer"], l["tokens"], l["granted_tokens"]) for l in a["layers"]]}),
                   "Pro-Ebene-Token + Gesamtsumme gegen Budget")
        except Exception as e:
            record("F078", "fail", note=str(e))

        c.close()

    # ---- F096: persistence across THREE separate core sessions (same HOME) ----
    try:
        seen = []
        for i in range(3):
            with core() as ctx:
                c = P.Client(ctx["sock"])
                a = c.request("context.assemble", {"cwd": str(ROOT)})
                seen.append(MEM_MARK in a["assembled_text"])
                c.close()
        assert all(seen), f"memory not persistent across sessions: {seen}"
        record("F096", "pass", ev("F096", "three-sessions.json",
               {"session_1": seen[0], "session_2": seen[1], "session_3": seen[2]}),
               "Fakt bleibt über 3 Core-Sessions erhalten")
    except Exception as e:
        record("F096", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
