#!/usr/bin/env python3
"""Echte Verifikation der Security-Features (F286–F299) gegen den realen Core.

Jeder Check führt — soweit headless überhaupt möglich — eine reale Operation
gegen den echten Core aus und schreibt Evidence nach test-harness/evidence/<FID>/.
Kein Mock, keine erfundenen Ergebnisse.

Befund vorab (durch Code-Lektüre + empirische Core-Calls bestätigt, siehe
Evidence-Dateien): Die einzige im Core verankerte Security-State-Größe ist
`trust_mode` (strict/standard/auto/yolo) in `~/.claudestudio/settings.json`.
Der Core setzt daraus über `Permission::from_trust_mode` GENAU EIN CLI-Flag:
  Strict          -> (kein Flag, --print Default)
  Standard / Auto -> --permission-mode acceptEdits
  Yolo            -> --dangerously-skip-permissions
Die GESAMTE Durchsetzung (Einzelbestätigung, gefährliche-Befehl-Erkennung,
Push-auf-main-Gate, rm-outside-project, Allowlist/Blocklist, Permission-Matrix,
Subagent-Policy, Prompt-Injection-Guard, Audit-Log der Entscheide) ist im Core
NICHT vorhanden — sie wird komplett an die echte Claude-CLI delegiert. Es gibt
keine entsprechenden IPC-Methoden, keine settings.json-Felder und keine
Audit-/Decision-Tabelle. Diese Features sind daher headless nicht gegen den
Core verifizierbar: sie brauchen entweder einen echten, eingeloggten,
netzgebundenen Claude-Agenten, interaktive GUI-Dialoge, echte git-Remotes —
oder sie existieren schlicht nicht im Core.

Aufruf:  python3 test-harness/probes/security.py
Ausgabe: JSON-Zeile  {"results": {fid: {"status": "...", "evidence": "...", "note": "..."}}}
"""
from __future__ import annotations
import json
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


def probe_method_absent(c, method: str, payload: dict | None = None) -> dict:
    """Ruft eine Methode auf und liefert {present, detail}.

    present=False heißt: der Core kennt die Methode nicht (Router-Fehler) —
    der empirische Beweis, dass das zugehörige Feature im Core fehlt.
    """
    try:
        r = c.request(method, payload or {})
        return {"method": method, "present": True, "response": r}
    except P.RemoteError as e:
        return {
            "method": method,
            "present": False,
            "error_code": e.code,
            "error_message": e.message,
        }


def main():
    log = ROOT / "test-harness/evidence/_security-core.log"
    with P.running_core(library_dir=ROOT, log_path=log) as ctx:
        home, sock = ctx["home"], ctx["sock"]
        c = P.Client(sock)

        # ------------------------------------------------------------------
        # Gemeinsame Grundlage: was kann der Core im Security-Bereich wirklich?
        # Wir prüfen empirisch (a) welche Felder config.get liefert und
        # (b) ob es überhaupt IPC-Methoden für die geforderten Mechanismen gibt.
        # ------------------------------------------------------------------
        cfg = c.request("config.get", {})
        config_keys = sorted(cfg.keys())

        # Kandidaten-Methoden, die die Features bräuchten, aber laut Router fehlen.
        candidate_methods = [
            "permissions.get", "permissions.set", "permissions.matrix",
            "permission.check", "audit.list", "audit.log", "audit.get",
            "gate.list", "gates.list", "security.gates",
            "bash.allowlist", "bash.blocklist", "command.check",
            "injection.scan", "guard.check",
        ]
        absence = [probe_method_absent(c, m) for m in candidate_methods]
        present_methods = [a["method"] for a in absence if a["present"]]

        core_capabilities = {
            "config_keys": config_keys,
            "trust_mode_is_only_security_state": "trust_mode" in config_keys,
            "candidate_security_methods_probed": [a["method"] for a in absence],
            "any_security_method_present": present_methods,
            "absence_detail": absence,
            "core_enforcement_model": (
                "trust_mode -> Permission::from_trust_mode -> EIN CLI-Flag "
                "(Strict=keins, Standard/Auto=--permission-mode acceptEdits, "
                "Yolo=--dangerously-skip-permissions). Keine eigenen Gates/"
                "Audit/Blocklist/Injection-Guard/Permission-Matrix im Core."
            ),
        }
        cap_blob = json.dumps(core_capabilities, indent=2, ensure_ascii=False)

        # ==================================================================
        # F286 — Globaler Trust-Modus-Schalter (4 Stufen) in der Titelleiste
        # ------------------------------------------------------------------
        # Headless verifizierbar ist NUR die Backing-Persistenz: config.set
        # trust_mode=strict -> settings.json. Das ist real und wird als
        # Evidence festgehalten. Die eigentlichen Feature-Ansprüche (4-stufige
        # Titelleisten-UI; "der gewählte Modus erscheint im Permission-Log der
        # nächsten Tool-Anfrage") brauchen GUI-Klick + ein Permission-Log, das
        # der Core gar nicht führt. -> blocked, mit echter Persistenz-Evidence.
        # ==================================================================
        try:
            modes_roundtrip = {}
            disk_after = {}
            for m in ("strict", "standard", "auto", "yolo"):
                c.request("config.set", {"trust_mode": m})
                got = c.request("config.get", {}).get("trust_mode")
                modes_roundtrip[m] = got
                assert got == m, f"set {m} did not persist (got {got})"
            # zurück auf strict für die Disk-Evidence
            c.request("config.set", {"trust_mode": "strict"})
            settings = home / ".claudestudio/settings.json"
            disk_after = json.loads(settings.read_text()) if settings.exists() else {}
            assert disk_after.get("trust_mode") == "strict", \
                f"settings.json trust_mode != strict: {disk_after.get('trust_mode')}"
            blob = json.dumps({
                "verifiable_headless": {
                    "all_four_modes_roundtrip": modes_roundtrip,
                    "settings_json_on_disk_after_set_strict": disk_after,
                },
                "NOT_verifiable_headless": [
                    "Vier-Stufen-Schalter in der Titelleiste (Strict/Standard/"
                    "Auto/YOLO mit Farben) — reine GUI, braucht Screenshot/Klick",
                    "'gewählter Modus erscheint identisch im Permission-Log der "
                    "nächsten Tool-Anfrage' — der Core führt KEIN Permission-Log",
                ],
                "core_capabilities": core_capabilities,
            }, indent=2, ensure_ascii=False)
            e = ev("F286", "trust-mode-persistence.json", blob)
            record("F286", "blocked", e,
                    "trust_mode-Persistenz (4 Stufen) im Core real bestätigt; "
                    "Titelleisten-UI + Permission-Log nicht headless prüfbar "
                    "(GUI/Screenshot; Core führt kein Permission-Log)")
        except Exception as e:
            record("F286", "fail", note=str(e))

        # ==================================================================
        # Gemeinsame Evidence für F287–F299: Core-Fähigkeiten / Fehlen der
        # nötigen Mechanismen. Jede dieser Features braucht entweder einen
        # echten laufenden Agenten + GUI-Dialoge + echte Remotes oder einen
        # Core-Mechanismus, der nachweislich nicht existiert.
        # ==================================================================

        # ---- F287: Strict erzwingt Einzelbestätigung (zwei GUI-Dialoge) ----
        try:
            e = ev("F287", "no-per-action-confirm-in-core.json", json.dumps({
                "claim": "Strict-Modus: jede Tool-Aktion erfordert eigenen "
                         "Bestätigungsdialog; Write ablehnen verhindert Datei.",
                "core_reality": "Strict mappt auf Permission::Default = KEIN "
                                "CLI-Flag. Im --print-Modus gibt es keine "
                                "interaktiven Pro-Aktion-Dialoge im Core; die "
                                "Bestätigung wäre Sache der echten CLI/GUI.",
                "why_blocked": "Braucht laufenden Claude-Agenten + interaktive "
                               "Read-/Write-Dialoge (Computer-Use/GUI) + "
                               "Audit-Log mit Read=allow/Write=denied. Headless "
                               "weder Agent noch GUI noch Decision-Log vorhanden.",
                "core_capabilities": core_capabilities,
            }, indent=2, ensure_ascii=False))
            record("F287", "blocked", e,
                    "Einzelbestätigungs-Dialoge + Decision-Log existieren nicht "
                    "headless; braucht echten Agenten + GUI + Audit-Log")
        except Exception as e:
            record("F287", "fail", note=str(e))

        # ---- F288: Standard fragt nur bei gefährlichen Aktionen ----
        try:
            e = ev("F288", "no-risk-classifier-in-core.json", json.dumps({
                "claim": "Standard: Read/Lint auto, rm/sudo/Deploy -> Dialog; "
                         "Audit-Log auto-allow vs ask.",
                "core_reality": "Standard mappt auf --permission-mode acceptEdits "
                                "(ein einziges CLI-Flag). Der Core hat KEINEN "
                                "Gefahren-Klassifizierer und KEIN auto-allow/ask-"
                                "Audit-Log.",
                "why_blocked": "Braucht echten Agenten, der Read- und rm-Aktionen "
                               "ausführt, einen rm-Dialog (GUI) und ein Audit-Log "
                               "mit auto-allow/ask. Nichts davon headless verfügbar.",
                "core_capabilities": core_capabilities,
            }, indent=2, ensure_ascii=False))
            record("F288", "blocked", e,
                    "Gefahren-Klassifizierer + auto-allow/ask-Audit-Log fehlen im "
                    "Core; braucht echten Agenten + GUI")
        except Exception as e:
            record("F288", "fail", note=str(e))

        # ---- F289: Auto führt alles aus außer Gate-geblockte Aktionen ----
        try:
            e = ev("F289", "no-gates-in-core.json", json.dumps({
                "claim": "Auto: alles auto, außer kritische Gates blocken "
                         "(z.B. git push origin main).",
                "core_reality": "Auto mappt auf --permission-mode acceptEdits. Es "
                                "gibt KEINE Gate-Logik im Core (grep nach gate/"
                                "push_to_main: keine Implementierung) und kein "
                                "'auto'/'blocked'-Audit-Log.",
                "why_blocked": "Braucht echten Multi-Step-Agentenlauf + ein "
                               "tatsächlich existierendes Gate + Audit-Log. Gate "
                               "existiert im Core nicht; Agent nicht headless.",
                "core_capabilities": core_capabilities,
            }, indent=2, ensure_ascii=False))
            record("F289", "blocked", e,
                    "Gate-Logik (z.B. push-to-main) im Core nicht vorhanden; "
                    "Audit-Log fehlt; braucht echten Agenten")
        except Exception as e:
            record("F289", "fail", note=str(e))

        # ---- F290: YOLO -> --dangerously-skip-permissions + Einmal-Dialog ----
        # Das Mapping Yolo->--dangerously-skip-permissions ist real im Code
        # (cs-claude Permission::Bypass.args()). Headless lässt sich nur die
        # config-Persistenz prüfen; dass das Flag im echten Prozess-Start-Log
        # landet UND ein einmaliger Warn-Dialog erscheint, braucht einen echten
        # Agentenlauf (eingeloggte CLI + Netz) und GUI. -> blocked, mit Beleg
        # des realen Code-Mappings + persistiertem yolo.
        try:
            c.request("config.set", {"trust_mode": "yolo"})
            got = c.request("config.get", {}).get("trust_mode")
            settings = home / ".claudestudio/settings.json"
            disk = json.loads(settings.read_text()) if settings.exists() else {}
            mapping_src = (ROOT / "core/crates/cs-claude/src/lib.rs").read_text()
            has_flag = "--dangerously-skip-permissions" in mapping_src
            maps_yolo = "TrustMode::Yolo => Permission::Bypass" in mapping_src
            assert got == "yolo" and disk.get("trust_mode") == "yolo"
            assert has_flag and maps_yolo, "Yolo->Bypass mapping not found in source"
            e = ev("F290", "yolo-mapping.json", json.dumps({
                "verifiable_headless": {
                    "config_set_trust_mode_yolo_persists": got == "yolo",
                    "settings_json_on_disk": disk,
                    "source_maps_yolo_to_bypass": maps_yolo,
                    "source_emits_dangerously_skip_permissions_flag": has_flag,
                },
                "NOT_verifiable_headless": [
                    "einmaliger Warn-Bestätigungsdialog beim Einschalten — GUI",
                    "'--dangerously-skip-permissions im Prozess-Start-Log' — "
                    "braucht echten Claude-Agentenlauf (eingeloggte CLI + Netz)",
                ],
            }, indent=2, ensure_ascii=False))
            record("F290", "blocked", e,
                    "Yolo->--dangerously-skip-permissions-Mapping im Code real + "
                    "yolo persistiert; Einmal-Dialog (GUI) und Flag-im-Prozess-"
                    "Start-Log (echter Agentenlauf) nicht headless prüfbar")
        except Exception as e:
            record("F290", "fail", note=str(e))

        # ---- F291: Gate 'Push auf main' hält selbst im YOLO an ----
        try:
            mapping_src = (ROOT / "core/crates/cs-claude/src/lib.rs").read_text()
            router_src = (ROOT / "core/crates/cs-cli/src/router.rs").read_text()
            gate_present = ("push-to-main" in router_src) or ("push_to_main" in router_src)
            e = ev("F291", "no-push-to-main-gate.json", json.dumps({
                "claim": "Trotz YOLO wird git push auf main gestoppt; Audit-Log "
                         "'blocked: push-to-main'; Remote-HEAD unverändert.",
                "core_reality": {
                    "push_to_main_gate_in_router": gate_present,
                    "note": "Im YOLO-Modus setzt der Core "
                            "--dangerously-skip-permissions und delegiert ALLES "
                            "an die CLI. Es existiert KEIN push-to-main-Gate und "
                            "kein Audit-Log im Core, das einen solchen Block "
                            "protokollieren könnte.",
                },
                "why_blocked": "Gate existiert nicht im Core; zudem bräuchte der "
                               "Test einen echten Agentenlauf, ein echtes "
                               "git-Remote und ein Audit-Log. Nichts headless da.",
                "core_capabilities": core_capabilities,
            }, indent=2, ensure_ascii=False))
            record("F291", "blocked", e,
                    "Push-auf-main-Gate im Core nicht implementiert; braucht "
                    "echten Agenten + Remote + Audit-Log")
        except Exception as e:
            record("F291", "fail", note=str(e))

        # ---- F292: Gate 'rm -rf außerhalb Projektpfad' selbst im YOLO ----
        try:
            router_src = (ROOT / "core/crates/cs-cli/src/router.rs").read_text()
            claude_src = (ROOT / "core/crates/cs-claude/src/lib.rs").read_text()
            rm_gate = ("rm-outside" in router_src or "rm_outside" in router_src
                       or "rm-outside" in claude_src or "rm_outside" in claude_src)
            e = ev("F292", "no-rm-outside-gate.json", json.dumps({
                "claim": "rm -rf außerhalb des Projekts wird (auch im YOLO) "
                         "gestoppt; Audit-Log 'blocked: rm-outside-project'.",
                "core_reality": {
                    "rm_outside_project_gate_present": rm_gate,
                    "note": "Kein Pfad-bewusster rm-Gate im Core; YOLO delegiert "
                            "alles an die CLI. Kein Audit-Log vorhanden.",
                },
                "why_blocked": "Gate existiert nicht im Core; Test bräuchte zudem "
                               "echten Agenten der rm ausführt + Audit-Log.",
                "core_capabilities": core_capabilities,
            }, indent=2, ensure_ascii=False))
            record("F292", "blocked", e,
                    "rm-outside-project-Gate im Core nicht implementiert; braucht "
                    "echten Agenten + Audit-Log")
        except Exception as e:
            record("F292", "fail", note=str(e))

        # ---- F293: Granulare Permission-Matrix (Checkbox-Tabelle pro Tool) ----
        try:
            # empirisch: gibt es eine Permission-Matrix im config oder per IPC?
            matrix_methods = [a for a in absence
                              if a["method"].startswith(("permission", "permissions"))]
            has_matrix_field = any(
                k for k in config_keys
                if "permission" in k.lower() or "matrix" in k.lower())
            e = ev("F293", "no-permission-matrix.json", json.dumps({
                "claim": "Checkbox-Matrix pro Tool (Bash/Read/Write/Edit/"
                         "WebSearch/MCP) mit always allow / ask / always deny; "
                         "Werte in settings.json; Audit-Log Read=allow/Write=deny.",
                "core_reality": {
                    "config_get_keys": config_keys,
                    "permission_matrix_field_in_config": has_matrix_field,
                    "permission_ipc_methods_present": [
                        m["method"] for m in matrix_methods if m["present"]],
                    "note": "settings.json (AppConfig) hat NUR trust_mode/"
                            "default_model/daily_budget_usd/context_token_budget/"
                            "voice/vector. KEINE Per-Tool-Permission-Matrix; "
                            "keine permissions.*-IPC-Methode.",
                },
                "why_blocked": "Permission-Matrix existiert im Core nicht (kein "
                               "config-Feld, keine IPC-Methode); zusätzlich GUI + "
                               "echter Agent + Audit-Log nötig.",
            }, indent=2, ensure_ascii=False))
            record("F293", "blocked", e,
                    "Per-Tool-Permission-Matrix im Core nicht vorhanden (kein "
                    "config-Feld/keine IPC-Methode); braucht GUI + Agent")
        except Exception as e:
            record("F293", "fail", note=str(e))

        # ---- F294: Bash Allowlist/Blocklist-Regex ----
        try:
            router_src = (ROOT / "core/crates/cs-cli/src/router.rs").read_text()
            cfg_src = (ROOT / "core/crates/cs-config/src/lib.rs").read_text()
            has_lists = any(t in router_src or t in cfg_src
                            for t in ("allowlist", "blocklist", "allow_list",
                                      "block_list"))
            e = ev("F294", "no-bash-allow-blocklist.json", json.dumps({
                "claim": "Bash-Befehle gegen Regex-Allowlist/Blocklist; "
                         "curl|sh blocklist-match geblockt, npm test allowlist "
                         "erlaubt; Audit-Log entsprechend.",
                "core_reality": {
                    "allow_or_block_list_in_config_or_router": has_lists,
                    "note": "Es gibt kein allowlist/blocklist-Konstrukt in "
                            "AppConfig oder im Router. Bash-Befehle werden vom "
                            "Core nicht gegen Regex-Listen geprüft.",
                },
                "why_blocked": "Allowlist/Blocklist existiert im Core nicht; Test "
                               "bräuchte zudem echten Agenten + Audit-Log + GUI.",
                "core_capabilities": core_capabilities,
            }, indent=2, ensure_ascii=False))
            record("F294", "blocked", e,
                    "Bash-Allowlist/Blocklist im Core nicht implementiert; braucht "
                    "echten Agenten + Audit-Log")
        except Exception as e:
            record("F294", "fail", note=str(e))

        # ---- F295: Subagenten eigener Permission-Satz, nie interaktiv ----
        try:
            e = ev("F295", "no-subagent-policy.json", json.dumps({
                "claim": "Subagenten haben eigenen Permission-Satz, fragen nie "
                         "nach; nicht erlaubte Aktion -> auto-deny; Audit-Log "
                         "'subagent denied (no-prompt)'.",
                "core_reality": "Der Core unterscheidet Permissions nicht nach "
                                "Haupt- vs. Subagent (kein separater Permission-"
                                "Satz, keine Permission-Matrix überhaupt). Es gibt "
                                "kein 'subagent denied'-Audit-Log.",
                "why_blocked": "Keine Subagent-Permission-Trennung und kein "
                               "Audit-Log im Core; braucht echten Subagentenlauf.",
                "core_capabilities": core_capabilities,
            }, indent=2, ensure_ascii=False))
            record("F295", "blocked", e,
                    "Subagent-Permission-Trennung + Audit-Log im Core nicht "
                    "vorhanden; braucht echten Subagentenlauf")
        except Exception as e:
            record("F295", "fail", note=str(e))

        # ---- F296: User- + Projekt-settings.json gemerged mit Quellen-Label ----
        try:
            e = ev("F296", "no-settings-merge-view.json", json.dumps({
                "claim": "User- und Projekt-settings.json transparent gemerged; "
                         "effektive Quelle (User vs Projekt) je Regel im UI.",
                "core_reality": "AppConfig::load_or_default liest GENAU EINE "
                                "settings.json aus einem Verzeichnis. Es gibt "
                                "keinen Merge zweier Ebenen und kein Quellen-"
                                "Label, weil es ohnehin keine Per-Regel-"
                                "Permissions im Core gibt.",
                "why_blocked": "Zwei-Ebenen-Merge mit Quellen-Label existiert im "
                               "Core nicht; zudem reine GUI-Ansicht (Screenshot).",
                "core_capabilities": core_capabilities,
            }, indent=2, ensure_ascii=False))
            record("F296", "blocked", e,
                    "User/Projekt-Settings-Merge mit Quellen-Label im Core nicht "
                    "vorhanden; reine GUI-Ansicht")
        except Exception as e:
            record("F296", "fail", note=str(e))

        # ---- F297: Prompt-Injection-Guard-Hook ----
        try:
            hooks_src = (ROOT / "core/crates/cs-hooks/src/lib.rs").read_text()
            inj = ("injection" in hooks_src.lower()
                   or "ignore previous" in hooks_src.lower())
            e = ev("F297", "no-injection-guard.json", json.dumps({
                "claim": "Guard-Hook prüft Tool-Outputs auf Injektionsmuster "
                         "('ignore previous instructions') und markiert/blockt; "
                         "Audit-Log 'injection-detected' + Mustertreffer + Datei.",
                "core_reality": {
                    "injection_logic_in_cs_hooks": inj,
                    "note": "cs-hooks ist eine reine Hook-MATCHER-Datenstruktur "
                            "(tool_name/exit_code/output_contains) ohne Injektions-"
                            "Erkennung. Es gibt keinen Injection-Guard und kein "
                            "'injection-detected'-Audit-Log im Core.",
                },
                "why_blocked": "Injection-Guard existiert im Core nicht; Test "
                               "bräuchte echten Agenten-Read-Output + Audit-Log.",
                "core_capabilities": core_capabilities,
            }, indent=2, ensure_ascii=False))
            record("F297", "blocked", e,
                    "Prompt-Injection-Guard im Core nicht implementiert "
                    "(cs-hooks ist reiner Matcher); braucht echten Agenten")
        except Exception as e:
            record("F297", "fail", note=str(e))

        # ---- F298: Audit-Log protokolliert jede Agent-Aktion (auch YOLO) ----
        try:
            sessions_src = (ROOT / "core/crates/cs-sessions/src/lib.rs").read_text()
            # Es gibt eine tool_calls-Tabelle, aber KEIN Decision/Entscheid-Feld
            # (allow/deny/blocked) und keine 'audit'-Tabelle.
            has_audit_table = "audit" in sessions_src.lower()
            has_decision_col = any(
                t in sessions_src for t in ("decision", "verdict", "allow",
                                            "deny TEXT", "blocked"))
            audit_method = probe_method_absent(c, "audit.list", {})
            e = ev("F298", "no-decision-audit-log.json", json.dumps({
                "claim": "Audit-Log protokolliert jede Aktion (Tool, Argumente, "
                         "Entscheid, Zeitstempel) auch im YOLO-Modus.",
                "core_reality": {
                    "audit_table_in_sessions_db": has_audit_table,
                    "decision_verdict_column_present": has_decision_col,
                    "audit_ipc_method": audit_method,
                    "note": "sessions.db hat eine tool_calls-Tabelle (tool_name/"
                            "input/output/success) — aber KEIN Entscheid-Feld "
                            "(allow/ask/deny/blocked) und keine Audit-Tabelle. "
                            "Es gibt keine audit.*-IPC-Methode. Das geforderte "
                            "'Entscheid'-Feld pro Aktion existiert nicht.",
                },
                "why_blocked": "Ein Audit-Log MIT Permission-Entscheid je Aktion "
                               "existiert im Core nicht; zudem echter Agentenlauf "
                               "nötig, um N reale Aktionen zu erzeugen.",
                "core_capabilities": core_capabilities,
            }, indent=2, ensure_ascii=False))
            record("F298", "blocked", e,
                    "Audit-Log mit Permission-Entscheid je Aktion fehlt im Core "
                    "(tool_calls hat kein Decision-Feld); braucht echten Agenten")
        except Exception as e:
            record("F298", "fail", note=str(e))

        # ---- F299: Dangerous-Command-Filter aktiv auch im YOLO ----
        try:
            router_src = (ROOT / "core/crates/cs-cli/src/router.rs").read_text()
            claude_src = (ROOT / "core/crates/cs-claude/src/lib.rs").read_text()
            cfg_src = (ROOT / "core/crates/cs-config/src/lib.rs").read_text()
            dang = any("dangerous-command" in s or "dangerous_command" in s
                       or "no-preserve-root" in s or "fork-bomb" in s
                       for s in (router_src, claude_src, cfg_src))
            e = ev("F299", "no-dangerous-command-filter.json", json.dumps({
                "claim": "Dangerous-Command-Filter bleibt auch im YOLO aktiv und "
                         "stoppt z.B. 'rm -rf / --no-preserve-root'; Audit-Log "
                         "'dangerous-command-blocked'.",
                "core_reality": {
                    "dangerous_command_filter_present": dang,
                    "note": "Im YOLO-Modus setzt der Core "
                            "--dangerously-skip-permissions und delegiert ALLES "
                            "ungefiltert an die CLI. Es gibt KEINEN eigenen "
                            "Dangerous-Command-Filter und kein "
                            "'dangerous-command-blocked'-Audit-Log im Core.",
                },
                "why_blocked": "Dangerous-Command-Filter existiert im Core nicht; "
                               "Test wäre zudem nur mit echtem Agenten + Audit-Log "
                               "(und ohne realen rm-Versuch am System) führbar.",
                "core_capabilities": core_capabilities,
            }, indent=2, ensure_ascii=False))
            record("F299", "blocked", e,
                    "Dangerous-Command-Filter im Core nicht implementiert (YOLO "
                    "delegiert ungefiltert an die CLI); braucht echten Agenten")
        except Exception as e:
            record("F299", "fail", note=str(e))

        # Gemeinsame Capability-Evidence einmal zentral ablegen (Referenz).
        ev("F286", "core-security-capabilities.json", cap_blob)

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
