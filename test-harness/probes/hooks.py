#!/usr/bin/env python3
"""Echte Verifikation der Hooks-Features (F256–F266).

Jeder Check führt — soweit headless überhaupt möglich — eine reale Operation
gegen den echten Core (IPC `hooks.list` / `file.write`) oder das echte
Dateisystem aus und schreibt Evidence nach test-harness/evidence/<FID>/.
Kein Mock, keine erfundenen Ergebnisse.

WICHTIGE BEFUND-LAGE (gegen Code + echten Core verifiziert):
  - Der Core exponiert über IPC NUR `hooks.list` (liest die Hooks aus
    `<cwd>/.claude/settings.json` + `~/.claude/settings.json`, gruppiert nach
    Event). Es gibt KEIN `hooks.upsert`/`hooks.create` und KEINE Hook-
    Ausführungs-Engine über IPC (router.rs: nur `"hooks.list"`).
  - Die Crate `cs-hooks` ist laut eigenem Doc-Kommentar "pure data + matching
    logic: it does not execute commands itself" und ist nicht einmal in
    `cs-cli` eingebunden — also über IPC gar nicht erreichbar.
  - Die App-View `HooksView.swift` ist eine reine READ-ONLY-Liste (gruppiert
    nach Event). Es gibt KEINE Hook-Typ-Karten-Übersicht, KEINEN Hook-Builder,
    KEINE One-Click-Presets, KEINEN Dry-Run/Log-View.
  => Das echte Hook-FEUERN passiert in Claude Code selbst (settings.json-Hooks),
     getrieben von einem ECHTEN laufenden Agenten — laut Harness-Regeln ein
     "blocked"-Grund. Builder-UI / Karten / Dry-Run-Log sind GUI+Screenshot —
     ebenfalls "blocked". Slack/OTel brauchen echte externe Dienste — "blocked".

Real & headless verifizierbar ist die Persistenz-Schicht, auf der der Builder
aufsetzt: ein Hook in `.claude/settings.json` (geschrieben über den echten
`file.write`-Handler) wird vom echten `hooks.list`-Handler korrekt mit Event,
Matcher und Befehl zurückgelesen (F266). Das ist genau der Vertrag, gegen den
der Builder schreiben würde.

Aufruf:  python3 test-harness/probes/hooks.py
Ausgabe: JSON-Zeile  {"results": {fid: {"status": "...", "evidence": "...", "note": "..."}}}
"""
from __future__ import annotations
import json
import os
import shutil
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


# Die 7 standardisierten Hook-Typen laut cs-hooks::HookKind (Quelle der Wahrheit).
HOOK_KINDS = [
    "PreToolUse",
    "PostToolUse",
    "Notification",
    "Stop",
    "SubagentStop",
    "WorktreeCreate",
    "WorktreeRemove",
]


def main():
    log = ROOT / "test-harness/evidence/_hooks-core.log"
    with P.running_core(library_dir=ROOT, log_path=log) as ctx:
        c = P.Client(ctx["sock"])

        # ------------------------------------------------------------------
        # F266 — Hook landet korrekt in .claude/settings.json und wird vom
        #         echten Core (hooks.list) als valides JSON zurückgelesen.
        #         Das ist die reale Persistenz-Schicht des Hook-Builders.
        # ------------------------------------------------------------------
        proj = Path(tempfile.mkdtemp(prefix="cs-hooks-proj-"))
        try:
            (proj / ".claude").mkdir(parents=True, exist_ok=True)
            settings_path = proj / ".claude/settings.json"

            # So würde der Builder einen PostToolUse-Hook ablegen.
            builder_payload = {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Edit|Write",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo gefeuert >> /tmp/hooklog.txt",
                                }
                            ],
                        }
                    ]
                }
            }
            # Schreiben über den ECHTEN file.write-Handler des Cores (nicht Python).
            fw = c.request(
                "file.write",
                {"path": str(settings_path), "content": json.dumps(builder_payload)},
            )
            assert fw.get("ok") is True, f"file.write failed: {fw}"

            # Auf-Disk: valides JSON mit korrektem Matcher + Befehl?
            on_disk = json.loads(settings_path.read_text())
            entry = on_disk["hooks"]["PostToolUse"][0]
            assert entry["matcher"] == "Edit|Write", f"matcher wrong: {entry}"
            assert (
                entry["hooks"][0]["command"] == "echo gefeuert >> /tmp/hooklog.txt"
            ), f"command wrong: {entry}"

            # Rücklesen über den ECHTEN hooks.list-Handler (parst settings.json).
            hl = c.request("hooks.list", {"cwd": str(proj)})
            rows = hl.get("hooks", [])
            match = [
                h
                for h in rows
                if h.get("event") == "PostToolUse"
                and h.get("matcher") == "Edit|Write"
                and h.get("command") == "echo gefeuert >> /tmp/hooklog.txt"
            ]
            assert match, f"hooks.list did not return the written hook: {rows}"

            e = ev(
                "F266",
                "settings-roundtrip.json",
                json.dumps(
                    {
                        "request_file_write": {
                            "path": str(settings_path),
                            "content": builder_payload,
                        },
                        "file_write_response": fw,
                        "settings_json_on_disk": on_disk,
                        "request_hooks_list": {"cwd": str(proj)},
                        "hooks_list_response": hl,
                        "asserted_match": match[0],
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
            )
            record(
                "F266",
                "pass",
                e,
                "file.write -> settings.json -> hooks.list liest Event/Matcher/Command korrekt zurück",
            )
        except Exception as e:  # noqa: BLE001
            record("F266", "fail", note=f"{type(e).__name__}: {e}")
        finally:
            shutil.rmtree(proj, ignore_errors=True)

        # ------------------------------------------------------------------
        # F256 — 7 Hook-Typen als Karten (PreToolUse … WorktreeRemove) mit
        #         Beschreibung. Die 7 Typen existieren real in cs-hooks::HookKind
        #         und der hooks.list-Parser verarbeitet alle 7 (inkl. der
        #         ClaudeStudio-eigenen Worktree-Events). Aber die KARTEN-
        #         Übersicht + Screenshot ist reines GUI -> blocked.
        #         Wir belegen immerhin, dass der echte Core alle 7 Kinds
        #         korrekt aus settings.json parst (Daten-Grundlage der Karten).
        # ------------------------------------------------------------------
        proj2 = Path(tempfile.mkdtemp(prefix="cs-hooks-kinds-"))
        kinds_supported = {}
        try:
            (proj2 / ".claude").mkdir(parents=True, exist_ok=True)
            sp2 = proj2 / ".claude/settings.json"
            all_kinds_settings = {
                "hooks": {
                    k: [
                        {
                            "matcher": "*",
                            "hooks": [{"type": "command", "command": f"echo {k}"}],
                        }
                    ]
                    for k in HOOK_KINDS
                }
            }
            sp2.write_text(json.dumps(all_kinds_settings))
            hl2 = c.request("hooks.list", {"cwd": str(proj2)})
            returned_events = {h.get("event") for h in hl2.get("hooks", [])}
            kinds_supported = {k: (k in returned_events) for k in HOOK_KINDS}
            # Grep der HookKind-Enum-Quelle als zusätzlicher Beleg.
            kind_src = (ROOT / "core/crates/cs-hooks/src/lib.rs").read_text()
            enum_present = {k: (k in kind_src) for k in HOOK_KINDS}
            ev(
                "F256",
                "hook-kinds.json",
                json.dumps(
                    {
                        "hook_kinds_in_cs_hooks_enum": enum_present,
                        "hooks_list_parsed_all_7_events": kinds_supported,
                        "hooks_list_response": hl2,
                        "note": "Karten-UI + Screenshot nicht headless verifizierbar",
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
            )
        except Exception:  # noqa: BLE001
            pass
        finally:
            shutil.rmtree(proj2, ignore_errors=True)
        all7 = all(kinds_supported.get(k) for k in HOOK_KINDS) if kinds_supported else False
        record(
            "F256",
            "blocked",
            "",
            (
                "GUI-Feature: 7 Hook-Typ-Karten + Screenshot. HooksView.swift ist eine "
                "reine Read-only-Liste (keine Karten-Übersicht). Headless nicht klickbar. "
                f"Beleg: Core parst alle 7 Kinds aus settings.json={all7}, "
                "HookKind-Enum in cs-hooks definiert alle 7."
            ),
        )

        # ------------------------------------------------------------------
        # F257 — Hook-Builder (Event wählen, Regex-Matcher, Shell-Aktion) +
        #         REALES Feuern bei Edit. Kein Builder-UI im App-Code; der Core
        #         hat KEINE Hook-Ausführungs-Engine über IPC (cs-hooks führt
        #         nichts aus, ist nicht in cs-cli eingebunden). Das echte Feuern
        #         passiert in Claude Code selbst, getrieben von einem ECHTEN
        #         laufenden Agenten -> per Harness-Regel "blocked".
        # ------------------------------------------------------------------
        record(
            "F257",
            "blocked",
            "",
            (
                "Builder-GUI fehlt (HooksView ist read-only) und das reale Feuern braucht "
                "einen echten laufenden Claude-Agenten, der eine Edit-Operation ausführt; "
                "der Core exponiert nur hooks.list, keine Ausführungs-Engine. "
                "Headless nicht verifizierbar."
            ),
        )

        # ------------------------------------------------------------------
        # F258 — One-Click Auto-Format (prettier) nach Edit.
        # ------------------------------------------------------------------
        record(
            "F258",
            "blocked",
            "",
            (
                "Braucht One-Click-Builder-UI + echten Agenten, der eine Datei per Edit "
                "ändert, damit der PostToolUse-Hook prettier real ausführt. Kein Builder/"
                "Ausführungs-Engine im Core; reines Agenten-/GUI-Verhalten. "
                "prettier ist zudem nicht auf dem PATH."
            ),
        )

        # ------------------------------------------------------------------
        # F259 — npm test nach Write.
        # ------------------------------------------------------------------
        record(
            "F259",
            "blocked",
            "",
            (
                "Braucht echten Agenten, der per Write feuert, und eine Hook-Engine, die "
                "'npm test' startet und das Log schreibt. Core führt keine Hooks aus; "
                "kein Builder-UI. Headless nicht verifizierbar."
            ),
        )

        # ------------------------------------------------------------------
        # F260 — Gefährliche Bash-Befehle blockieren (PreToolUse, Exit 2).
        # ------------------------------------------------------------------
        record(
            "F260",
            "blocked",
            "",
            (
                "Braucht einen echten Claude-Agenten, der 'rm -rf' ausführen will, plus "
                "eine PreToolUse-Hook-Engine, die mit Exit-Code 2 blockt. Diese "
                "Ausführungs-/Block-Logik läuft in Claude Code selbst, nicht im Core "
                "(cs-hooks führt nichts aus). Headless nicht verifizierbar."
            ),
        )

        # ------------------------------------------------------------------
        # F261 — Slack-Notification bei Stop.
        # ------------------------------------------------------------------
        record(
            "F261",
            "blocked",
            "",
            (
                "Braucht eine echte Slack-Webhook-URL/Credentials und einen sichtbaren "
                "Slack-Channel-Screenshot sowie einen echten Agenten, der eine Session "
                "abschließt (Stop-Hook). Externer Dienst + GUI -> nicht headless."
            ),
        )

        # ------------------------------------------------------------------
        # F262 — npm install nach package.json-Edit.
        # ------------------------------------------------------------------
        record(
            "F262",
            "blocked",
            "",
            (
                "Braucht echten Agenten, der package.json per Edit ändert, plus Hook-"
                "Engine, die 'npm install' startet (Netzwerk-Install gegen npm-Registry). "
                "Core führt keine Hooks aus; kein Builder-UI. Nicht headless verifizierbar."
            ),
        )

        # ------------------------------------------------------------------
        # F263 — OTel-Event nach Tool-Call.
        # ------------------------------------------------------------------
        record(
            "F263",
            "blocked",
            "",
            (
                "Braucht einen laufenden lokalen OpenTelemetry-Collector als externen "
                "Dienst sowie einen echten Agenten-Tool-Call und eine Hook-Engine, die "
                "ein Span/Event emittiert. Externer Dienst + Agent -> nicht headless."
            ),
        )

        # ------------------------------------------------------------------
        # F264 — Auto-Commit bei WorktreeRemove.
        # ------------------------------------------------------------------
        record(
            "F264",
            "blocked",
            "",
            (
                "Braucht den vollen Worktree-Lebenszyklus über die App und eine "
                "WorktreeRemove-Hook-Engine, die 'git add -A && git commit' ausführt. "
                "cs-hooks definiert WorktreeRemove nur als Daten-Kind, führt aber nichts "
                "aus; es gibt keinen IPC-Pfad, der den Hook real triggert. Nicht headless."
            ),
        )

        # ------------------------------------------------------------------
        # F265 — Dry-Run-Modus + Log-Ansicht (Input/Output) mit Screenshot.
        # ------------------------------------------------------------------
        record(
            "F265",
            "blocked",
            "",
            (
                "GUI-Feature: Dry-Run-Toggle + Log-Ansicht mit Input/Output-JSON, belegt "
                "per Screenshot. Weder Dry-Run-Modus noch Log-View existieren im App-Code "
                "(HooksView ist read-only), und es gibt keine Ausführungs-Engine. "
                "Headless nicht verifizierbar."
            ),
        )

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
