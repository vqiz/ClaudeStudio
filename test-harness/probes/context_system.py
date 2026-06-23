#!/usr/bin/env python3
"""Echte Verifikation der Context-System-Features (F076–F086).

Jeder Check führt — soweit headless überhaupt möglich — eine reale Operation
gegen den echten ClaudeStudio-Core aus (IPC über den length-prefixed-MessagePack-
Socket, echtes Dateisystem unter einem isolierten HOME) und schreibt Evidence
nach test-harness/evidence/<FID>/. Kein Mock, keine erfundenen Ergebnisse.

Wichtiger Befund vorab (empirisch gegen den echten Core bestätigt):
  * Der Core kennt KEINE assembled-context-Methode (`dumpAssembledContext` /
    `context.assemble` -> 404). Die einzige Kontext-IPC ist `context.budget`,
    deren Pro-Ebene-Tokenwerte FEST verdrahtet sind (1200/3000/2400/6000/4000/
    800) und NICHT aus den realen CLAUDE.md-Dateien gelesen werden.
  * Der Editor (Swift `EditableFileView`) ist ein simpler `TextEditor` auf Basis
    von `file.read`/`file.write` — OHNE Live-Token-Counter, OHNE >4000-Warnung,
    OHNE Sektions-Editor, OHNE Diff-Ansicht, OHNE Backup-vor-Speichern, OHNE
    "In Test-Session laden"-Button.
  * Es existieren KEINE FastAPI/Next.js/React-Native-Templates (nur ein
    generisches CLAUDE.md/AGENTS.md-Starter), KEIN AGENTS.md-Frontmatter-Formular
    und KEIN Pro-Ebene-Toggle (config.set ignoriert solche Keys).

Darum sind die meisten Features dieser Kategorie ehrlich "blocked": die im
real_world_test geforderte Fähigkeit ist headless nicht verifizierbar UND im
Core/in der App gar nicht vorhanden. Wo ein realer Core-Teilbeweis möglich ist
(z.B. file.write -> Disk -> file.read mit Marker, oder das echte
context.budget-Verhalten), wird er als Evidence festgehalten — aber ein Feature
wird nur dann "pass", wenn seine spezifische Behauptung wirklich gegen den echten
Core hielt.

Aufruf:  python3 test-harness/probes/context_system.py
Ausgabe: JSON-Zeile  {"results": {fid: {"status": ..., "evidence": ..., "note": ...}}}
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def est_tokens_chars4(text: str) -> int:
    """Der Token-Heuristik des Cores nachgebaut: ceil(chars/4)."""
    n = len(text)
    return (n + 3) // 4


def tiktoken_count(text: str):
    """Unabhängige Zählung mit tiktoken, falls verfügbar (sonst None)."""
    try:
        import tiktoken  # type: ignore
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return None


def method_absent(c, method: str) -> tuple[bool, str]:
    """True + Fehlertext, wenn der Core die Methode mit 404 ablehnt."""
    try:
        c.request(method, {})
        return (False, "method exists (returned a response)")
    except P.RemoteError as e:
        return (e.code == 404, f"[{e.code}] {e.message}")
    except Exception as e:  # noqa: BLE001
        return (False, f"unexpected: {e}")


def main():
    log = ROOT / "test-harness/evidence/_context-system-core.log"
    with P.running_core(library_dir=ROOT, log_path=log) as ctx:
        home, sock = ctx["home"], ctx["sock"]
        c = P.Client(sock)

        global_md = home / ".claude" / "CLAUDE.md"
        project_dir = home / "testproj"
        project_md = project_dir / ".claude" / "CLAUDE.md"

        # =================================================================
        # F076 — 6-Ebenen-Lade-Reihenfolge / dumpAssembledContext
        # =================================================================
        # Der real_world_test verlangt: Sentinels aus realen Dateien je Ebene,
        # zusammengebauter Kontext per IPC (dumpAssembledContext), Byte-Offsets
        # aufsteigend L1<L2<...<L6. Diese assembled-context-Methode EXISTIERT im
        # Core nicht. Belegt wird der 404 für alle plausiblen Methodennamen sowie
        # die einzige real vorhandene Ebenen-Quelle (context.budget) mit ihrer
        # kanonischen 6-Ebenen-Reihenfolge.
        try:
            sentinels = {
                global_md: "SENTINEL_L1",
                project_md: "SENTINEL_L3",
            }
            for path, marker in sentinels.items():
                c.request("file.write", {"path": str(path), "content": f"# {marker}\n"})

            probes = {}
            for m in ("dumpAssembledContext", "context.assemble", "context.dump",
                      "context.layers", "context.get", "context.assembled"):
                absent, msg = method_absent(c, m)
                probes[m] = msg

            budget = c.request("context.budget", {})
            layer_order = [l["layer"] for l in budget.get("layers", [])]
            expected_order = [
                "global_claude_md", "cross_project_memory", "project_claude_md",
                "vector_retrieval", "active_definitions", "worktree_override",
            ]
            order_ok = layer_order == expected_order

            e = ev("F076", "assembled-context-absent.json", json.dumps({
                "real_world_test_requires": "IPC dumpAssembledContext of file-derived sentinels in ascending byte order",
                "assemble_dump_method_probes": probes,
                "only_layer_source_present": "context.budget",
                "context_budget_layer_order": layer_order,
                "expected_priority_order": expected_order,
                "priority_order_matches": order_ok,
                "note": "context.budget exposes the canonical 6-layer order but uses HARDCODED per-layer token estimates and never reads the sentinel files; no assembled-context dump exists.",
                "verdict": "blocked — no assembled-context dump method; sentinel byte-offset test not executable headless",
            }, indent=2, ensure_ascii=False))
            record("F076", "blocked", e,
                   "Kein dumpAssembledContext/context.assemble im Core (404); Sentinel-Reihenfolge-Test nicht ausführbar")
        except Exception as e:  # noqa: BLE001
            record("F076", "fail", note=f"unerwartet: {e}")

        # =================================================================
        # F077 — Pro-Ebene Toggle (an/aus), abgeschaltete Ebene weglassen
        # =================================================================
        # Im Core/Config existiert kein Layer-Toggle: config.set ignoriert solche
        # Keys still und es gibt keinen assembled-context-Dump, in dem ein
        # weggelassener L3-Block sichtbar wäre. Empirisch belegt.
        try:
            before_keys = set(c.request("config.get", {}).keys())
            toggle_attempts = {}
            for key in ("layers", "enabled_layers", "context_layers",
                        "layer_enabled", "disabled_layers"):
                try:
                    r = c.request("config.set", {key: {"project_claude_md": False}})
                    toggle_attempts[key] = ("present_in_response"
                                            if key in r else "silently_ignored")
                except P.RemoteError as e2:
                    toggle_attempts[key] = f"[{e2.code}] {e2.message}"
            after_keys = set(c.request("config.get", {}).keys())
            no_new_field = before_keys == after_keys
            assemble_absent, assemble_msg = method_absent(c, "dumpAssembledContext")

            e = ev("F077", "no-layer-toggle.json", json.dumps({
                "config_keys_before": sorted(before_keys),
                "config_set_toggle_attempts": toggle_attempts,
                "config_keys_after": sorted(after_keys),
                "config_unchanged_by_toggle_keys": no_new_field,
                "assembled_context_dump": assemble_msg,
                "verdict": "blocked — no per-layer enable/disable capability in core or config; cannot disable L3 and re-dump",
            }, indent=2, ensure_ascii=False))
            record("F077", "blocked", e,
                   "Kein Pro-Ebene-Toggle in Core/Config (config.set ignoriert Layer-Keys); nicht verifizierbar")
        except Exception as e:  # noqa: BLE001
            record("F077", "fail", note=f"unerwartet: {e}")

        # =================================================================
        # F078 — Context-Budget-Anzeige (Pro-Ebene + Gesamt gegen Budget)
        # =================================================================
        # context.budget IST real und zeigt Pro-Ebene-Tokens + Gesamt gegen das
        # konfigurierte Budget mit korrekter greedy-Truncation. ABER: die Werte
        # sind FEST verdrahtet und werden NICHT aus dem realen Datei-Inhalt von
        # L1/L3 berechnet. Der real_world_test verlangt aber gerade, dass die
        # ANGEZEIGTE Gesamttokenzahl der unabhängigen Zählung der eingefüllten
        # Datei-Texte entspricht — das kann der Core nicht leisten. Wir belegen
        # das echte Budget-Verhalten und die fehlende Datei-Kopplung; die
        # eigentliche Behauptung des Tests hält nicht -> blocked.
        try:
            # Budget auf 10000 setzen (wie im Test gefordert) und echtes Verhalten lesen.
            c.request("config.set", {"context_token_budget": 10000})
            budget = c.request("context.budget", {})
            layer_tokens = {l["layer"]: l["granted_tokens"] for l in budget["layers"]}
            granted_total = budget["granted_total"]
            total_budget = budget["total_budget"]

            # L1/L3 mit Texten bekannter Tokenzahl füllen (wie der Test es vorsieht)…
            l1_text = "word " * 400   # ~ bekannte Zahl
            l3_text = "token " * 600
            c.request("file.write", {"path": str(global_md), "content": l1_text})
            c.request("file.write", {"path": str(project_md), "content": l3_text})
            indep_l1 = {"chars4": est_tokens_chars4(l1_text), "tiktoken": tiktoken_count(l1_text)}
            indep_l3 = {"chars4": est_tokens_chars4(l3_text), "tiktoken": tiktoken_count(l3_text)}

            # …und prüfen, ob das Budget DARAUF reagiert (tut es nicht):
            budget_after_fill = c.request("context.budget", {})
            l1_granted = next(l for l in budget_after_fill["layers"]
                              if l["layer"] == "global_claude_md")["granted_tokens"]
            l3_granted = next(l for l in budget_after_fill["layers"]
                              if l["layer"] == "project_claude_md")["granted_tokens"]
            reacts_to_files = not (l1_granted == 1200 and l3_granted in (2400,
                                   max(0, 10000 - 1200 - 3000)))  # heuristisch

            total_matches_independent = abs(
                granted_total - (indep_l1["chars4"] + indep_l3["chars4"])
            ) <= 0.02 * granted_total

            e = ev("F078", "budget-not-file-driven.json", json.dumps({
                "config_budget_set_to": 10000,
                "context_budget_total_budget": total_budget,
                "context_budget_per_layer_granted": layer_tokens,
                "context_budget_granted_total": granted_total,
                "display_format_would_be": f"{granted_total}/{total_budget}",
                "filled_L1_independent_tokens": indep_l1,
                "filled_L3_independent_tokens": indep_l3,
                "L1_granted_after_fill": l1_granted,
                "L3_granted_after_fill": l3_granted,
                "budget_reacts_to_file_content": reacts_to_files,
                "displayed_total_matches_independent_file_count_within_2pct": total_matches_independent,
                "verdict": "blocked — context.budget uses HARDCODED per-layer estimates, never the real file token counts; the test's core assertion (displayed total == independent count of L1/L3 file texts) cannot hold, and reading the SwiftUI budget bar is headless-unavailable",
            }, indent=2, ensure_ascii=False))
            record("F078", "blocked", e,
                   "context.budget existiert, ignoriert aber Datei-Tokens (feste Werte); Bar-Screenshot headless n/a")
        except Exception as e:  # noqa: BLE001
            record("F078", "fail", note=f"unerwartet: {e}")

        # =================================================================
        # F079 — Global-CLAUDE.md-Editor mit Live-Token-Counter
        # =================================================================
        # Die Speicher-auf-Disk-Hälfte ist real (file.write -> Disk -> file.read)
        # und wird hier ECHT durchgeführt (EDIT_MARKER_F079 landet wirklich auf
        # der Platte). Aber der Editor (EditableFileView) hat KEINEN Token-Counter
        # — das ist genau die F079 unterscheidende Fähigkeit gegenüber dem reinen
        # Datei-Edit (F003). Da diese Fähigkeit fehlt und der angezeigte Count
        # headless ohnehin nicht lesbar ist, kann F079 nicht "pass" sein.
        try:
            # Echter Round-Trip: Marker schreiben, von Disk lesen.
            new_content = "# Global\nEDIT_MARKER_F079\n"
            wr = c.request("file.write", {"path": str(global_md), "content": new_content})
            disk = global_md.read_text()
            rd = c.request("file.read", {"path": str(global_md)})
            marker_on_disk = "EDIT_MARKER_F079" in disk
            marker_via_ipc = "EDIT_MARKER_F079" in rd.get("content", "")
            indep_count = {"chars4": est_tokens_chars4(disk), "tiktoken": tiktoken_count(disk)}

            assert marker_on_disk and marker_via_ipc, "marker did not persist"

            # Belegen, dass der Editor keinen Token-Counter hat (Swift-Quelle).
            editor_src = (ROOT / "app/Sources/ClaudeStudio/Views/EditableFileView.swift").read_text()
            has_token_counter = ("token" in editor_src.lower())

            e = ev("F079", "global-editor-edit.json", json.dumps({
                "file_write_response": wr,
                "disk_contains_EDIT_MARKER_F079": marker_on_disk,
                "file_read_contains_EDIT_MARKER_F079": marker_via_ipc,
                "independent_token_count_of_new_file": indep_count,
                "editor_view_has_token_counter (EditableFileView.swift)": has_token_counter,
                "verdict": "blocked — the file edit/persist sub-part works for real (marker on disk), but the editor has NO live token counter (Monaco/counter not present in EditableFileView) and the displayed count is headless-unavailable; F079's distinguishing capability is unimplemented",
            }, indent=2, ensure_ascii=False))
            record("F079", "blocked", e,
                   "Datei-Edit/Persistenz real bewiesen (Marker auf Disk), aber Live-Token-Counter fehlt im Editor")
        except Exception as e:  # noqa: BLE001
            record("F079", "fail", note=f"unerwartet: {e}")

        # =================================================================
        # F080 — Token-Warnung >4000 im Global-Editor
        # =================================================================
        # Reine UI-Warnung; EditableFileView besitzt keinerlei Token-/Warn-Logik.
        try:
            editor_src = (ROOT / "app/Sources/ClaudeStudio/Views/EditableFileView.swift").read_text()
            has_warning = any(k in editor_src for k in ("4000", "4_000", "warn", "Warn"))
            e = ev("F080", "no-token-warning.json", json.dumps({
                "editor_source": "app/Sources/ClaudeStudio/Views/EditableFileView.swift",
                "contains_4000_or_warning_logic": has_warning,
                "verdict": "blocked — no >4000-token warning exists in the editor; a visible UI warning is headless-unavailable anyway",
            }, indent=2, ensure_ascii=False))
            record("F080", "blocked", e,
                   "Keine >4000-Token-Warnung im Editor implementiert; UI-Warnung headless nicht prüfbar")
        except Exception as e:  # noqa: BLE001
            record("F080", "fail", note=f"unerwartet: {e}")

        # =================================================================
        # F081 — Sektions-Editor (vorgefertigte Markdown-Sektionen)
        # =================================================================
        # Kein Sektions-Editor vorhanden: weder im Core noch in der App gibt es
        # eine Funktion, die '# Coding Preferences' o.ä. einfügt.
        try:
            hits = subprocess.run(
                ["grep", "-rniE", "Coding Preferences|Sektions|section editor|section-editor",
                 str(ROOT / "app/Sources"), str(ROOT / "core/crates")],
                capture_output=True, text=True)
            found = [l for l in hits.stdout.splitlines() if "target/" not in l]
            e = ev("F081", "no-section-editor.json", json.dumps({
                "grep_pattern": "Coding Preferences|Sektions|section editor",
                "matches": found,
                "verdict": "blocked — no section editor that injects predefined markdown headings exists",
            }, indent=2, ensure_ascii=False))
            record("F081", "blocked", e,
                   "Kein Sektions-Editor ('Coding Preferences' etc.) in Core/App vorhanden")
        except Exception as e:  # noqa: BLE001
            record("F081", "fail", note=f"unerwartet: {e}")

        # =================================================================
        # F082 — Diff zum letzten Stand vor dem Speichern
        # =================================================================
        # EditableFileView zeigt keinen Diff; es gibt nur ein 'unsaved'-Badge.
        try:
            editor_src = (ROOT / "app/Sources/ClaudeStudio/Views/EditableFileView.swift").read_text()
            has_diff = any(k in editor_src for k in ("diff", "Diff", "hunk", "Hunk"))
            e = ev("F082", "no-diff-view.json", json.dumps({
                "editor_source": "app/Sources/ClaudeStudio/Views/EditableFileView.swift",
                "contains_diff_logic": has_diff,
                "verdict": "blocked — no diff view between buffer and last-saved version exists; visual diff is headless-unavailable",
            }, indent=2, ensure_ascii=False))
            record("F082", "blocked", e,
                   "Keine Diff-Ansicht im Editor implementiert; visueller Diff headless nicht prüfbar")
        except Exception as e:  # noqa: BLE001
            record("F082", "fail", note=f"unerwartet: {e}")

        # =================================================================
        # F083 — Automatisches Backup vor Änderung
        # =================================================================
        # file.write erstellt NACHWEISLICH kein Backup. Zwei echte Schreibvorgänge
        # auf dieselbe Datei -> kein Backup-Verzeichnis, keine zeitgestempelte
        # Kopie der Vorversion.
        try:
            bpath = home / ".claude" / "CLAUDE.md"
            c.request("file.write", {"path": str(bpath), "content": "BACKUP_TEST_V1"})
            c.request("file.write", {"path": str(bpath), "content": "BACKUP_TEST_V2"})
            cs = home / ".claudestudio"
            listing = subprocess.run(
                ["find", str(home / ".claude"), str(cs), "-iname", "*backup*", "-o", "-type", "d", "-iname", "backups"],
                capture_output=True, text=True).stdout
            backups_dir = cs / "backups"
            full = subprocess.run(["find", str(home / ".claude"), str(cs)],
                                  capture_output=True, text=True).stdout
            backup_present = backups_dir.exists() or "backup" in full.lower()
            e = ev("F083", "no-backup.json", json.dumps({
                "writes": ["BACKUP_TEST_V1", "BACKUP_TEST_V2"],
                "backups_dir_exists": backups_dir.exists(),
                "any_backup_file_found": backup_present,
                "filesystem_after_two_writes": full,
                "verdict": "blocked — file.write performs NO backup-before-save; no backup directory or timestamped copy is created",
            }, indent=2, ensure_ascii=False))
            record("F083", "blocked", e,
                   "file.write legt kein Backup an (kein backups/-Verzeichnis nach zwei Schreibvorgängen)")
        except Exception as e:  # noqa: BLE001
            record("F083", "fail", note=f"unerwartet: {e}")

        # =================================================================
        # F084 — 'In Test-Session laden'-Button injiziert L1
        # =================================================================
        # Es gibt weder einen solchen Button noch eine IPC, die den Kontext einer
        # Session abfragt (session.* liefert Transkripte/Stats, keinen assembled
        # context). Ohne assembled-context-Dump nicht verifizierbar.
        try:
            c.request("file.write", {"path": str(global_md), "content": "TESTLOAD_F084\n"})
            probes = {}
            for m in ("session.context", "session.assembled_context",
                      "context.load_into_session", "dumpAssembledContext"):
                _, msg = method_absent(c, m)
                probes[m] = msg
            e = ev("F084", "no-test-session-load.json", json.dumps({
                "marker_written_to_global": "TESTLOAD_F084",
                "session_context_method_probes": probes,
                "verdict": "blocked — no 'load into test session' action and no IPC to read a session's assembled context (session.* returns transcripts/stats only); marker-in-L1 assertion not executable",
            }, indent=2, ensure_ascii=False))
            record("F084", "blocked", e,
                   "Kein 'In Test-Session laden' + keine Session-Kontext-IPC; L1-Marker nicht prüfbar")
        except Exception as e:  # noqa: BLE001
            record("F084", "fail", note=f"unerwartet: {e}")

        # =================================================================
        # F085 — Projekt-CLAUDE.md-Editor mit Templates (FastAPI/Next.js/RN)
        # =================================================================
        # Die geforderten Frameworks-Templates existieren nirgends. ContextTemplates
        # bietet nur ein generisches CLAUDE.md/AGENTS.md ohne 'uvicorn'/'FastAPI'.
        try:
            tpl_path = ROOT / "app/Sources/ClaudeStudio/Views/ContextTemplates.swift"
            tpl_src = tpl_path.read_text()
            hits = subprocess.run(
                ["grep", "-rniE", "fastapi|uvicorn|next\\.?js|react native|react-native",
                 str(ROOT / "app/Sources"), str(ROOT / "core/crates")],
                capture_output=True, text=True)
            framework_matches = [l for l in hits.stdout.splitlines() if "target/" not in l]
            has_fastapi_template = any(k in tpl_src for k in ("uvicorn", "FastAPI"))
            e = ev("F085", "no-framework-templates.json", json.dumps({
                "context_templates_file": "app/Sources/ClaudeStudio/Views/ContextTemplates.swift",
                "context_templates_has_fastapi_markers": has_fastapi_template,
                "repo_wide_framework_template_matches": framework_matches,
                "available_templates": ["generic CLAUDE.md", "generic AGENTS.md"],
                "verdict": "blocked — no FastAPI/Next.js/React Native templates exist; only a generic CLAUDE.md/AGENTS.md starter, so the FastAPI-marker assertion cannot hold",
            }, indent=2, ensure_ascii=False))
            record("F085", "blocked", e,
                   "Keine FastAPI/Next.js/RN-Templates vorhanden (nur generisches CLAUDE.md); FastAPI-Marker fehlt")
        except Exception as e:  # noqa: BLE001
            record("F085", "fail", note=f"unerwartet: {e}")

        # =================================================================
        # F086 — AGENTS.md-Tab visueller Frontmatter-Editor (YAML)
        # =================================================================
        # Es gibt kein Frontmatter-Formular und keinen YAML-Frontmatter-Writer für
        # AGENTS.md. Die generische AGENTS.md-Vorlage hat überhaupt keinen
        # '---'-Frontmatter-Block; ClaudeMdAgents schreibt nur einen markierten
        # Markdown-Block, kein name/description/tools-YAML.
        try:
            tpl_src = (ROOT / "app/Sources/ClaudeStudio/Views/ContextTemplates.swift").read_text()
            agents_md_template = tpl_src  # generische Vorlage enthält keinen ----Block
            has_frontmatter_block = "\n---\n" in ContextTemplates_agents(tpl_src)
            hits = subprocess.run(
                ["grep", "-rniE", "frontmatter|front-matter|yaml.*agents|name:.*description:",
                 str(ROOT / "app/Sources")],
                capture_output=True, text=True)
            fm_matches = [l for l in hits.stdout.splitlines() if "target/" not in l]
            e = ev("F086", "no-agents-frontmatter-editor.json", json.dumps({
                "agents_md_template_has_yaml_frontmatter_block": has_frontmatter_block,
                "frontmatter_editor_grep_matches": fm_matches,
                "verdict": "blocked — no visual frontmatter form and no YAML-frontmatter writer for AGENTS.md; the generic AGENTS.md template has no '---' block, so name='build-agent'/tools YAML cannot be produced/parsed",
            }, indent=2, ensure_ascii=False))
            record("F086", "blocked", e,
                   "Kein AGENTS.md-Frontmatter-Formular/YAML-Writer; Vorlage hat keinen ---Block")
        except Exception as e:  # noqa: BLE001
            record("F086", "fail", note=f"unerwartet: {e}")

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


def ContextTemplates_agents(tpl_src: str) -> str:
    """Extrahiert (best effort) den agentsMd-String aus der Swift-Vorlage; dient
    nur dazu, das Fehlen eines YAML-Frontmatter-Blocks zu belegen."""
    marker = "static let agentsMd = \"\"\""
    i = tpl_src.find(marker)
    if i == -1:
        return ""
    j = tpl_src.find("\"\"\"", i + len(marker))
    return tpl_src[i + len(marker): j] if j != -1 else tpl_src[i:]


if __name__ == "__main__":
    main()
