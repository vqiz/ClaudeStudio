#!/usr/bin/env python3
"""Echte Verifikation der File-Explorer-Features (F046–F063).

Jeder Check führt — soweit headless überhaupt möglich — eine reale Operation
gegen den echten Core / das echte Dateisystem / ein echtes git-Repo aus und
schreibt Evidence nach test-harness/evidence/<FID>/. Kein Mock.

WICHTIGER BEFUND (empirisch gegen den echten Core bestätigt, siehe
evidence/_file-explorer-capabilities.json):
Der Rust-Core exponiert für die Dateiverwaltung NUR `file.read`, `file.write`
sowie die git-Methoden (`git.status`, `git.diff`, `git.branch`, `git.log`,
`git.worktrees`). Es gibt KEINE IPC-Methoden für:
  - rekursiven Dateibaum / Verzeichnis-Listing (file.tree, fs.list, project.tree)
  - Datei anlegen/umbenennen/verschieben/löschen/duplizieren
    (file.create/rename/move/delete/duplicate)
  - Volltext-/Fuzzy-/ripgrep-Suche (search.*, grep, ripgrep, files.search)
  - notify-rs-Watcher (watch.start, fs.watch)
  - Asset-/Vector-Node-Erzeugung (assets.create, vector.add)
Alle diese Aufrufe liefern reproduzierbar `[404] unknown method`.

Zusätzlich existiert in der Swift-App KEINE Dateibaum-/Explorer-View: die
Projektansicht zeigt Kontextdateien (CLAUDE.md/AGENTS.md), Worktrees, git-Status
und Sessions — aber keinen navigierbaren Dateibaum mit Status-Indikatoren,
Kontextmenüs, Drag&Drop, Inline-Vorschau oder Suche.

Konsequenz nach den ehernen Ehrlichkeitsregeln:
  - Kein File-Explorer-Feature lässt sich headless als FUNKTIONIEREND beweisen.
  - Jedes Feature ist entweder GUI-only (interaktiver Baum, Kontextmenü,
    Drag&Drop, Hover-Popover, Farbgebung) oder es fehlt die nötige
    Core-Fähigkeit komplett. Beides -> status "blocked" mit präzisem Grund.
  - Wo eine zugrundeliegende Core-Primitive (file.read, git.status, git.diff)
    EXISTIERT, exerzieren wir sie real und legen die echte Request+Response als
    Evidence ab — damit der "blocked"-Grund belegt ist (Primitive vorhanden,
    aber die sichtbare Funktion ist headless nicht prüfbar / das Verdrahten
    fehlt).

Aufruf:  python3 test-harness/probes/file_explorer.py
Ausgabe: JSON-Zeile  {"results": {fid: {"status": "...", "evidence": "...", "note": "..."}}}
"""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


# Methoden, die ein echter File-Explorer im Core bräuchte — gruppiert pro Feature.
# Wir prüfen empirisch, dass sie NICHT existieren (404 unknown method).
NEEDED_METHODS = {
    "F046": ["file.tree", "fs.list", "project.tree"],
    "F047": ["file.create"],
    "F048": ["file.rename"],
    "F049": ["file.move"],
    "F050": ["file.delete"],
    "F051": ["file.duplicate"],
    "F052": ["file.status_flags", "file.indicators"],
    "F053": ["session.attach_file", "session.attach"],
    "F054": ["file.reveal_in_finder", "editor.open"],
    "F055": ["session.attach_file"],
    "F056": ["assets.create", "vector.add", "brain.add_asset"],
    "F057": ["file.preview"],
    "F058": [],  # git.status existiert — Farbgebung ist GUI
    "F059": [],  # git.diff existiert — Indikator ist GUI
    "F060": ["search.fuzzy", "files.search"],
    "F061": ["search.text", "search.grep", "ripgrep", "grep"],
    "F062": ["watch.start", "fs.watch"],
    "F063": ["project.tree", "workspace.roots"],
}


def probe_absence(c, methods: list[str]) -> dict:
    """Ruft jede Methode mit {} auf und sammelt das echte Fehlerverhalten."""
    out = {}
    for m in methods:
        try:
            r = c.request(m, {})
            out[m] = {"exists": True, "response": r}
        except P.RemoteError as e:
            out[m] = {"exists": False, "code": e.code, "message": e.message}
        except Exception as e:  # noqa: BLE001
            out[m] = {"exists": False, "error": f"{type(e).__name__}: {e}"}
    return out


def all_absent(probe: dict) -> bool:
    return probe and all(not v.get("exists") for v in probe.values())


def main():
    log = ROOT / "test-harness/evidence/_file-explorer-core.log"

    # Ein echtes git-Repo als Fixture für F058/F059 (echter git-Status/-Diff).
    fixture = Path(tempfile.mkdtemp(prefix="cs-fe-invoice-app-"))
    git_env = {"GIT_AUTHOR_NAME": "probe", "GIT_AUTHOR_EMAIL": "p@e",
               "GIT_COMMITTER_NAME": "probe", "GIT_COMMITTER_EMAIL": "p@e"}

    def git(*args):
        return subprocess.run(["git", "-C", str(fixture), *args],
                              capture_output=True, text=True, env={**git_env})

    capabilities: dict[str, dict] = {}

    try:
        # --- git-Fixture aufbauen: committen, dann ändern/neu/löschen --------
        subprocess.run(["git", "init", "-q", str(fixture)], capture_output=True)
        (fixture / "invoice.ts").write_text("export const total = 1;\nexport const tax = 0;\n")
        (fixture / "alt.ts").write_text("export const old = true;\n")
        git("add", "-A")
        git("commit", "-q", "-m", "initial")
        # Änderungen, die git.status klassifizieren soll:
        (fixture / "invoice.ts").write_text("export const total = 42;\nexport const tax = 0;\n")  # modified
        (fixture / "neu.ts").write_text("export const fresh = 1;\n")  # untracked/new
        (fixture / "alt.ts").unlink()  # deleted

        with P.running_core(library_dir=ROOT, log_path=log) as ctx:
            c = P.Client(ctx["sock"])

            # ---- Sanity: Core lebt --------------------------------------
            assert c.request("ping", {}).get("pong") is True

            # ---- Empirischer Fähigkeits-Scan über ALLE benötigten Methoden
            for fid, methods in NEEDED_METHODS.items():
                if methods:
                    capabilities[fid] = probe_absence(c, methods)

            # Globale Evidence über fehlende Fähigkeiten ablegen.
            cap_path = P.write_evidence(
                "_file-explorer", "capabilities.json",
                json.dumps({
                    "note": "Empirischer Scan: welche File-Explorer-Methoden der echte Core kennt.",
                    "existing_file_methods": ["file.read", "file.write"],
                    "existing_git_methods": ["git.status", "git.diff", "git.branch",
                                             "git.log", "git.worktrees"],
                    "probed": capabilities,
                }, indent=2, ensure_ascii=False))
            cap_rel = str(cap_path.relative_to(ROOT))

            # ===========================================================
            # F046 — rekursiver Dateibaum in der Sidebar (GUI + fehlt Core)
            # ===========================================================
            pr = capabilities["F046"]
            assert all_absent(pr), f"unerwartet vorhandene Methode: {pr}"
            e = ev("F046", "no-tree-method.json", json.dumps(
                {"feature": "rekursiver Projekt-Dateibaum",
                 "probe": pr,
                 "swift_app": "keine Dateibaum-/Explorer-View vorhanden (ProjectsView/"
                              "ProjectWorkspaceView zeigen nur Kontextdateien/Worktrees/Sessions)"},
                indent=2, ensure_ascii=False))
            record("F046", "blocked", e,
                   "Core kennt keine Tree-/Listing-Methode (404) und es existiert keine "
                   "Dateibaum-GUI; rekursive Baumdarstellung ist headless nicht prüfbar")

            # ===========================================================
            # F047 — Datei anlegen via Kontextmenü
            # ===========================================================
            pr = capabilities["F047"]
            assert all_absent(pr), pr
            e = ev("F047", "no-create-method.json", json.dumps(pr, indent=2, ensure_ascii=False))
            record("F047", "blocked", e,
                   "Core kennt keine file.create-Methode (404); Anlegen läuft nur über das "
                   "GUI-Kontextmenü, das headless nicht bedienbar ist")

            # ===========================================================
            # F048 — Datei umbenennen via Kontextmenü
            # ===========================================================
            pr = capabilities["F048"]
            assert all_absent(pr), pr
            e = ev("F048", "no-rename-method.json", json.dumps(pr, indent=2, ensure_ascii=False))
            record("F048", "blocked", e,
                   "Core kennt keine file.rename-Methode (404); Umbenennen nur via "
                   "GUI-Kontextmenü, headless nicht prüfbar")

            # ===========================================================
            # F049 — Datei verschieben via Drag&Drop
            # ===========================================================
            pr = capabilities["F049"]
            assert all_absent(pr), pr
            e = ev("F049", "no-move-method.json", json.dumps(pr, indent=2, ensure_ascii=False))
            record("F049", "blocked", e,
                   "Core kennt keine file.move-Methode (404); Verschieben ist Drag&Drop im "
                   "Baum — reine GUI-Interaktion, headless nicht durchführbar")

            # ===========================================================
            # F050 — Datei löschen via Kontextmenü + Bestätigungsdialog
            # ===========================================================
            pr = capabilities["F050"]
            assert all_absent(pr), pr
            e = ev("F050", "no-delete-method.json", json.dumps(pr, indent=2, ensure_ascii=False))
            record("F050", "blocked", e,
                   "Core kennt keine file.delete-Methode (404); Löschen erfordert "
                   "GUI-Kontextmenü + Bestätigungsdialog, headless nicht prüfbar")

            # ===========================================================
            # F051 — Datei duplizieren via Kontextmenü
            # ===========================================================
            pr = capabilities["F051"]
            assert all_absent(pr), pr
            e = ev("F051", "no-duplicate-method.json", json.dumps(pr, indent=2, ensure_ascii=False))
            record("F051", "blocked", e,
                   "Core kennt keine file.duplicate-Methode (404); Duplizieren nur via "
                   "GUI-Kontextmenü, headless nicht prüfbar")

            # ===========================================================
            # F052 — Claude-Status-Indikatoren pro Datei (Schloss/Stern/Edit)
            # ===========================================================
            pr = capabilities["F052"]
            assert all_absent(pr), pr
            e = ev("F052", "no-indicator-method.json", json.dumps(pr, indent=2, ensure_ascii=False))
            record("F052", "blocked", e,
                   "Keine Core-Methode für Datei-Statusflags (404); die Indikatorspalte "
                   "(geschützt/Asset/bearbeitet) ist eine reine GUI-Darstellung, headless "
                   "nicht betrachtbar")

            # ===========================================================
            # F053 — geschützte Pfade (.env) beim Session-Anhängen blockieren
            # ===========================================================
            # Es gibt KEINEN Session-Attach-Pfad-Filter im Core. file.read liest
            # JEDE Datei ungefiltert (inkl. .env) — d.h. die schützende Funktion
            # existiert nicht. Wir belegen real: file.read gibt .env-Inhalt zurück.
            envfile = fixture / ".env"
            envfile.write_text("SECRET=abc123\n")
            read_env = c.request("file.read", {"path": str(envfile)})
            pr = capabilities["F053"]
            leaked = "abc123" in (read_env.get("content") or "")
            e = ev("F053", "protected-path-not-enforced.json", json.dumps(
                {"attach_methods_probe": pr,
                 "file_read_on_dotenv": read_env,
                 "secret_leaked_via_file_read": leaked,
                 "conclusion": "Kein Schutzmechanismus: weder eine Attach-Methode mit "
                               "Pfadfilter noch eine Sperre in file.read. .env-Inhalt wird "
                               "ungefiltert zurückgegeben."},
                indent=2, ensure_ascii=False))
            record("F053", "blocked", e,
                   "Geschützte-Pfade-Sperre existiert nicht im Core; es gibt keine "
                   "Session-Attach-Methode mit Filter und file.read liefert .env "
                   "(SECRET=abc123) ungefiltert — Funktion unimplementiert, headless als "
                   "'blockiert' nicht beweisbar")

            # ===========================================================
            # F054 — Schnell-Aktionen (Im Finder zeigen / In Monaco öffnen)
            # ===========================================================
            # 'In Monaco öffnen' setzt voraus, den echten Dateiinhalt zu laden —
            # diese Primitive (file.read) existiert und liefert echten Inhalt.
            # 'Im Finder zeigen' / Monaco-Fenster sind GUI/AppKit, headless nicht prüfbar.
            ppath = fixture / "pipeline.py"
            ppath.write_text("def run():\n    return 'pipeline'\n")
            read_py = c.request("file.read", {"path": str(ppath)})
            content_ok = read_py.get("content") == "def run():\n    return 'pipeline'\n"
            pr = capabilities["F054"]
            e = ev("F054", "quick-actions.json", json.dumps(
                {"open_in_monaco_primitive_file_read": read_py,
                 "content_matches_disk": content_ok,
                 "reveal_in_finder_and_editor_methods_probe": pr,
                 "conclusion": "file.read liefert den echten Inhalt (Monaco-Quelle), aber "
                               "'Im Finder zeigen' und das tatsächliche Editor-Fenster sind "
                               "AppKit-/GUI-Aktionen ohne Core-Methode — headless nicht "
                               "ausführbar/beobachtbar."},
                indent=2, ensure_ascii=False))
            assert content_ok, "file.read lieferte nicht den erwarteten Inhalt"
            record("F054", "blocked", e,
                   "file.read liefert echten Inhalt (Monaco-Quelle belegt), aber 'Im Finder "
                   "zeigen' / Editor-Fenster sind GUI-AppKit-Aktionen ohne Core-Methode — "
                   "headless nicht beobachtbar")

            # ===========================================================
            # F055 — Drag&Drop Datei auf Session: Pfad + Inhalt anhängen
            # ===========================================================
            idx = fixture / "index.ts"
            idx.write_text("export const ok = 1;\n")
            read_idx = c.request("file.read", {"path": str(idx)})
            pr = capabilities["F055"]
            e = ev("F055", "drag-attach.json", json.dumps(
                {"file_read_for_content": read_idx,
                 "session_attach_method_probe": pr,
                 "conclusion": "file.read kann Pfad+Inhalt liefern, ABER es existiert keine "
                               "session.attach_file-Methode und kein Drag&Drop-Ziel headless; "
                               "die eigentliche Anhänge-Operation ist nicht testbar."},
                indent=2, ensure_ascii=False))
            record("F055", "blocked", e,
                   "Drag&Drop auf das Session-Eingabefeld ist GUI; keine "
                   "session.attach_file-Methode im Core (404). file.read liefert zwar "
                   "Pfad+Inhalt, das Anhängen selbst ist headless nicht durchführbar")

            # ===========================================================
            # F056 — Drag&Drop auf Brain-Graph -> Asset-Node in Vector-DB
            # ===========================================================
            pr = capabilities["F056"]
            assert all_absent(pr), pr
            e = ev("F056", "no-asset-node-method.json", json.dumps(pr, indent=2, ensure_ascii=False))
            record("F056", "blocked", e,
                   "Keine Methode zum Anlegen eines Asset-/Vector-Nodes (404); Drag&Drop auf "
                   "den Brain-Graph ist GUI und die Vector-DB-Einbettung beim Drop ist "
                   "unimplementiert")

            # ===========================================================
            # F057 — Inline-Vorschau beim Hovern (SVG/Markdown gerendert)
            # ===========================================================
            pr = capabilities["F057"]
            assert all_absent(pr), pr
            e = ev("F057", "no-preview-method.json", json.dumps(pr, indent=2, ensure_ascii=False))
            record("F057", "blocked", e,
                   "Keine file.preview-Methode (404); Hover-Popover mit gerendertem "
                   "SVG/Markdown ist reine GUI-Darstellung, headless nicht beobachtbar")

            # ===========================================================
            # F058 — Git-Status-Farben (modified/new/deleted) im Baum
            # ===========================================================
            # Core-Primitive git.status EXISTIERT und klassifiziert die States.
            # Wir belegen real, dass sie invoice.ts=Modified, neu.ts=Untracked,
            # alt.ts=Deleted liefert — exakt wie der echte git status.
            status = c.request("git.status", {"cwd": str(fixture)})
            raw_git = git("status", "--porcelain").stdout
            by_path = {e2["path"]: e2 for e2 in status.get("entries", [])}
            inv = by_path.get("invoice.ts", {})
            neu = by_path.get("neu.ts", {})
            alt = by_path.get("alt.ts", {})
            states_ok = (
                inv.get("state") == "modified"
                and neu.get("state") == "untracked"
                and alt.get("state") == "deleted"
            )
            e = ev("F058", "git-status-states.json", json.dumps(
                {"git_status_ipc": status,
                 "git_porcelain_terminal": raw_git,
                 "classified": {"invoice.ts": inv.get("state"),
                                "neu.ts": neu.get("state"),
                                "alt.ts": alt.get("state")},
                 "states_match_expected": states_ok,
                 "conclusion": "Die Core-Primitive git.status klassifiziert die States KORREKT "
                               "(modified/untracked/deleted) — das ist die Datenbasis für die "
                               "Farbgebung. Die tatsächliche Farb-Darstellung (orange/grün/"
                               "durchgestrichen) im Dateibaum ist jedoch GUI und headless nicht "
                               "betrachtbar; zudem fehlt der Dateibaum in der App."},
                indent=2, ensure_ascii=False))
            assert states_ok, f"git.status klassifizierte States falsch: {by_path}"
            record("F058", "blocked", e,
                   "git.status liefert korrekt modified/untracked/deleted (Evidence belegt) — "
                   "aber die Farbgebung im Dateibaum ist GUI und der Baum existiert nicht; "
                   "die sichtbare Funktion ist headless nicht prüfbar")

            # ===========================================================
            # F059 — Diff-Indikator zeigt echten Diff zur committeten Version
            # ===========================================================
            # Core-Primitive git.diff EXISTIERT (ganzes Repo, nicht pro Datei).
            diff_ipc = c.request("git.diff", {"cwd": str(fixture)})
            raw_diff = git("diff").stdout
            diff_str = diff_ipc.get("diff", "")
            # Der IPC-Diff muss die echte Änderung an invoice.ts enthalten.
            has_change = "invoice.ts" in diff_str and "+export const total = 42;" in diff_str
            matches_terminal = diff_str.strip() == raw_diff.strip()
            e = ev("F059", "git-diff.json", json.dumps(
                {"git_diff_ipc": {"staged": diff_ipc.get("staged"),
                                  "diff_len": len(diff_str)},
                 "diff_contains_invoice_change": has_change,
                 "ipc_diff_equals_terminal_diff": matches_terminal,
                 "git_diff_ipc_full": diff_str,
                 "git_diff_terminal_full": raw_diff,
                 "conclusion": "git.diff liefert den echten Diff (identisch zur Terminal-"
                               "Ausgabe), ABER nur für das GESAMTE Repo (kein Pfad-Argument), "
                               "und der klickbare Diff-Indikator pro Datei im Baum ist GUI — "
                               "headless nicht prüfbar; der Dateibaum fehlt zudem."},
                indent=2, ensure_ascii=False))
            assert has_change and matches_terminal, "git.diff stimmt nicht mit Terminal überein"
            record("F059", "blocked", e,
                   "git.diff liefert den echten, mit dem Terminal identischen Diff (Evidence) "
                   "— aber nur repo-weit (kein Per-Datei-Argument) und der Diff-Indikator im "
                   "Baum ist GUI; headless nicht klickbar/prüfbar")

            # ===========================================================
            # F060 — Fuzzy-Dateiname-Suche
            # ===========================================================
            pr = capabilities["F060"]
            assert all_absent(pr), pr
            e = ev("F060", "no-fuzzy-search.json", json.dumps(pr, indent=2, ensure_ascii=False))
            record("F060", "blocked", e,
                   "Keine Fuzzy-Such-Methode im Core (search.fuzzy/files.search -> 404); die "
                   "Schnellsuche mit Zeichen-Hervorhebung ist unimplementiert")

            # ===========================================================
            # F061 — Volltextsuche per ripgrep (via Rust-Core)
            # ===========================================================
            pr = capabilities["F061"]
            assert all_absent(pr), pr
            e = ev("F061", "no-ripgrep-search.json", json.dumps(pr, indent=2, ensure_ascii=False))
            record("F061", "blocked", e,
                   "Keine ripgrep-/Volltext-Such-Methode im Core (search.text/grep/ripgrep -> "
                   "404); die Funktion existiert nicht — kein Treffer mit Datei+Zeilennummer "
                   "abrufbar")

            # ===========================================================
            # F062 — notify-rs Watcher synchronisiert den Baum live
            # ===========================================================
            pr = capabilities["F062"]
            assert all_absent(pr), pr
            e = ev("F062", "no-watcher-method.json", json.dumps(pr, indent=2, ensure_ascii=False))
            record("F062", "blocked", e,
                   "Kein notify-rs-Watcher im Core (watch.start/fs.watch -> 404; notify-Crate "
                   "wird nicht verwendet); Live-Sync des Baums ist unimplementiert und der "
                   "Baum selbst fehlt")

            # ===========================================================
            # F063 — Cross-Project-Modus-Toggle (mehrere Wurzeln)
            # ===========================================================
            pr = capabilities["F063"]
            assert all_absent(pr), pr
            e = ev("F063", "no-multiroot-method.json", json.dumps(pr, indent=2, ensure_ascii=False))
            record("F063", "blocked", e,
                   "Keine Multi-Root-/Workspace-Methode im Core (404); der "
                   "Cross-Project-Toggle und parallele Projektwurzeln im Baum sind GUI und "
                   "unimplementiert")

            c.close()
    finally:
        shutil.rmtree(fixture, ignore_errors=True)

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
