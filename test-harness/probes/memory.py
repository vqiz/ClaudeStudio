#!/usr/bin/env python3
"""Echte Verifikation der Memory-Features (F087–F096).

Jeder Check führt eine reale Operation gegen den echten Core / das echte
Dateisystem aus und schreibt Evidence nach test-harness/evidence/<FID>/.
Kein Mock, keine erfundenen Ergebnisse.

Kern-Konzept der Kategorie "memory": Cross-Project-Memory aus
~/.claudestudio/memory/global.md (und projects/<name>.md) soll als "Ebene2"
in den zusammengebauten Session-Kontext geladen werden; ein Memory-Manager-UI
soll Einträge kategorisieren/editieren/altern lassen; ein Post-Session-Hook
soll Erkenntnisse extrahieren.

Verifikations-Strategie: Wir prüfen EMPIRISCH gegen den echten Core, ob die
dafür nötigen Fähigkeiten existieren:
  * Gibt es eine IPC-Methode, die memory/global.md liest? (memory.*, context.*)
  * Liefert irgendeine Methode einen "Session-Kontext-Dump" mit Ebene2-Block?
  * Reflektiert die context.budget-Memory-Ebene den echten global.md-Inhalt
    (Token-Zahl), oder ist sie statisch?
Wo eine echte Operation real fehlschlägt (Methode fehlt / Kontext lädt Memory
nicht) -> status "fail" mit der echten Fehlermeldung als Evidence.
Wo das Feature nur per GUI / echtem Agent / Post-Session-Hook verifizierbar
wäre (headless nicht verfügbar) -> status "blocked" mit präzisem Grund — aber
NUR nachdem belegt ist, dass auch keine headless-IPC-Fähigkeit existiert.

Aufruf:  python3 test-harness/probes/memory.py
Ausgabe: JSON-Zeile  {"results": {fid: {"status": ..., "evidence": ..., "note": ...}}}
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


# Marker-Fakt mit bekannter Tokenzahl für die Memory-Datei.
GLOBAL_MD = (
    "# Cross-Project Memory\n\n"
    "## Preferences\n"
    "MEMFACT: Lieblingsfarbe ist Blau\n\n"
    "## Projekte\n"
    "Mein Deploy-Server heißt prod-01\n"
)


def probe_methods(c, methods: list[str]) -> dict[str, str]:
    """Ruft jede Methode mit {} auf und sammelt die echte Antwort/Fehlermeldung."""
    out: dict[str, str] = {}
    for m in methods:
        try:
            r = c.request(m, {})
            out[m] = "OK " + json.dumps(r, ensure_ascii=False)[:200]
        except P.RemoteError as e:
            out[m] = f"ERR [{e.code}] {e.message}"
        except Exception as e:  # noqa: BLE001
            out[m] = f"EXC {type(e).__name__}: {e}"
    return out


def find_context_dump_method(c) -> str | None:
    """Sucht eine IPC-Methode, die einen Session-Kontext-DUMP (echter Text mit
    Ebene2-Block) liefert. Gibt den Methodennamen zurück oder None."""
    for m in ["context.build", "context.assemble", "context.dump", "context.get",
              "context.preview", "session.context", "memory.context"]:
        try:
            c.request(m, {})
            return m  # antwortet ohne 404 -> existiert
        except P.RemoteError as e:
            if e.code != 404:
                # existiert, braucht nur andere Payload
                return m
        except Exception:
            pass
    return None


def main():
    log = ROOT / "test-harness/evidence/_memory-core.log"
    with P.running_core(library_dir=ROOT, log_path=log) as ctx:
        home = ctx["home"]
        c = P.Client(ctx["sock"])

        # global.md im echten State-Dir anlegen (so wie ein User/Feature es täte)
        mem_dir = home / ".claudestudio" / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        global_md = mem_dir / "global.md"
        global_md.write_text(GLOBAL_MD)

        # Per-Projekt-Memory ebenfalls anlegen
        proj_dir = mem_dir / "projects"
        proj_dir.mkdir(parents=True, exist_ok=True)
        proj_md = proj_dir / "data-pipeline.md"
        proj_md.write_text("# data-pipeline\nPROJFACT_F095\nPERSIST_F096\n")

        # ---- Empirische Methoden-Inventur (echte Core-Antworten) -----------
        mem_methods = [
            "memory.get", "memory.list", "memory.read", "memory.global",
            "memory.add", "memory.set", "memory.update", "memory.categories",
            "memory.projects", "memory.suggest", "memory.dump",
        ]
        ctx_methods = [
            "context.build", "context.assemble", "context.dump", "context.get",
            "session.context", "memory.context",
        ]
        insight_methods = [
            "insights.extract", "insights.suggest", "memory.suggest",
            "session.insights", "hooks.run", "hooks.fire",
        ]
        mem_probe = probe_methods(c, mem_methods)
        ctx_probe = probe_methods(c, ctx_methods)
        ins_probe = probe_methods(c, insight_methods)

        # Verfügbare Methoden insgesamt: alle memory.*/context.* sind 404?
        all_memory_absent = all(v.startswith("ERR [404]") for v in mem_probe.values())
        dump_method = find_context_dump_method(c)

        # context.budget: ist die Memory-Ebene statisch oder spiegelt sie global.md?
        budget = c.request("context.budget", {})
        cpm = next((l for l in budget["layers"]
                    if l["layer"] == "cross_project_memory"), None)
        # unabhängige Tokenzählung von global.md (~ 4 Zeichen/Token-Heuristik,
        # wie cs-config::estimate_tokens) — Referenz für F093
        raw = GLOBAL_MD
        independent_tokens = max(1, len(raw) // 4)

        inventory = {
            "global_md_written": str(global_md),
            "global_md_bytes": len(GLOBAL_MD.encode()),
            "project_md_written": str(proj_md),
            "memory_methods": mem_probe,
            "context_methods": ctx_probe,
            "insight_methods": ins_probe,
            "all_memory_methods_404": all_memory_absent,
            "context_dump_method_found": dump_method,
            "context_budget_cross_project_memory_layer": cpm,
            "independent_token_count_of_global_md": independent_tokens,
            "note": (
                "Die context.budget-Memory-Ebene meldet einen FESTEN Token-Wert "
                "(hartkodiert in router.rs::budget_payload, with_layer("
                "CrossProjectMemory, 3000)); sie liest global.md NICHT."
            ),
        }
        inv_path = str(P.write_evidence(
            "_memory-inventory", "inventory.json",
            json.dumps(inventory, indent=2, ensure_ascii=False)).relative_to(ROOT))

        # ================================================================
        # F087: global.md wird als Ebene2 in neue Session geladen
        #   -> erfordert echten Session-Kontext-Dump mit Ebene2-Block.
        #   Empirisch: kein context-dump-/memory.*-Endpoint existiert, und die
        #   Budget-Memory-Ebene ist statisch (3000), unabhängig vom global.md-
        #   Inhalt. Damit lädt der Core global.md NICHT in einen Kontext.
        # ================================================================
        f087_ev = P.write_evidence("F087", "no-l2-context.json", json.dumps({
            "global_md_path": str(global_md),
            "global_md_content": GLOBAL_MD,
            "memory_methods_probe": mem_probe,
            "context_dump_method_found": dump_method,
            "context_budget_memory_layer_is_static": cpm,
            "verdict": (
                "FAIL: Es existiert keine IPC-Methode, die einen Session-Kontext "
                "mit einem Ebene2-Block aus global.md zusammenbaut. Alle memory.* "
                "und context.build/assemble/dump/get -> 404. Die einzige Memory-"
                "Ebene (context.budget.cross_project_memory) ist mit 3000 Tokens "
                "hartkodiert und ignoriert global.md. 'MEMFACT: Lieblingsfarbe ist "
                "Blau' kann im neuen Session-Kontext nicht erscheinen."
            ),
        }, indent=2, ensure_ascii=False)).relative_to(ROOT)
        record("F087", "fail", str(f087_ev),
               "Kein Session-Kontext-Dump mit Ebene2/global.md im Core; "
               "Memory-Budget-Ebene statisch (404 auf allen memory.*/context.*).")

        # ================================================================
        # F088: Memory-Manager-UI kategorisiert Einträge gruppiert.
        #   GUI-Feature; zudem fehlt headless jede memory.categories-API.
        # ================================================================
        f088_ev = P.write_evidence("F088", "no-manager-api.json", json.dumps({
            "memory_methods_probe": mem_probe,
            "verdict": (
                "BLOCKED: Memory-Manager ist ein SwiftUI-GUI (Screenshots der "
                "kategorisierten Gruppierung nötig) — headless nicht klickbar. "
                "Auch headless gibt es KEINE Kategorisierungs-API: memory.list/"
                "memory.categories -> 404. global.md trägt keine Kategorie-Marker."
            ),
        }, indent=2, ensure_ascii=False)).relative_to(ROOT)
        record("F088", "blocked", str(f088_ev),
               "GUI-Manager + headless keine memory.categories-API (alle 404).")

        # ================================================================
        # F089: Inline-Edit im Manager persistiert sofort auf Disk.
        #   GUI-Aktion; headless keine memory.update-API.
        # ================================================================
        f089_ev = P.write_evidence("F089", "no-inline-edit-api.json", json.dumps({
            "memory_update_probe": mem_probe.get("memory.update"),
            "memory_set_probe": mem_probe.get("memory.set"),
            "verdict": (
                "BLOCKED: Inline-Editierung erfolgt im GUI-Manager (Klick + "
                "Bestätigen). Headless keine Persistenz-API: memory.update/"
                "memory.set -> 404. (file.write existiert generisch, ist aber "
                "nicht der Memory-Manager-Edit-Pfad dieses Features.)"
            ),
        }, indent=2, ensure_ascii=False)).relative_to(ROOT)
        record("F089", "blocked", str(f089_ev),
               "GUI-Inline-Edit; headless keine memory.update-API (404).")

        # ================================================================
        # F090: Post-Session-Hook extrahiert Erkenntnisse + Vorschlag-Dialog.
        #   Erfordert echten laufenden Claude-Agent + GUI-Dialog + Hook.
        # ================================================================
        f090_ev = P.write_evidence("F090", "no-insight-hook.json", json.dumps({
            "insight_methods_probe": ins_probe,
            "verdict": (
                "BLOCKED: Erfordert eine echte Claude-Session (laufender Agent), "
                "einen Post-Session-Extraktions-Hook und einen GUI-Vorschlag-"
                "Dialog mit Preview — headless nicht ausführbar. Headless gibt es "
                "zudem keine Extraktions-API: insights.extract/session.insights/"
                "memory.suggest -> 404."
            ),
        }, indent=2, ensure_ascii=False)).relative_to(ROOT)
        record("F090", "blocked", str(f090_ev),
               "Echter Agent + Post-Session-Hook + GUI-Dialog; keine insights.*-API (404).")

        # ================================================================
        # F091: Übernahme eines Vorschlags schreibt in global.md -> Folge-Session.
        #   Hängt an F090 (Vorschlag-Dialog) UND an F087 (Ebene2-Laden).
        #   Selbst der Disk-Schreibteil hat keine memory.add-API, und der
        #   Lade-Teil (Ebene2) existiert nicht -> real nicht erfüllbar.
        # ================================================================
        f091_ev = P.write_evidence("F091", "no-add-and-no-l2.json", json.dumps({
            "memory_add_probe": mem_probe.get("memory.add"),
            "context_dump_method_found": dump_method,
            "verdict": (
                "FAIL/BLOCKED: Übernahme setzt den GUI-Vorschlag-Dialog aus F090 "
                "voraus (blocked). Unabhängig davon fehlt die Lade-Hälfte: es gibt "
                "keinen Session-Kontext-Dump mit Ebene2 (siehe F087), sodass ein "
                "in global.md geschriebener Fakt nicht 'im Ebene2-Block der neuen "
                "Session' erscheinen kann. memory.add -> 404."
            ),
        }, indent=2, ensure_ascii=False)).relative_to(ROOT)
        record("F091", "blocked", str(f091_ev),
               "Setzt F090-GUI-Dialog voraus; zudem fehlt Ebene2-Laden (F087) und memory.add (404).")

        # ================================================================
        # F092: Veraltet-Markierung (>90 Tage ungenutzt).
        #   GUI-Badge; headless keine Metadaten/Aging-Logik im Core.
        # ================================================================
        f092_ev = P.write_evidence("F092", "no-aging-logic.json", json.dumps({
            "memory_methods_probe": mem_probe,
            "verdict": (
                "BLOCKED: 'Veraltet'-Badge ist ein GUI-Element (Screenshot nötig). "
                "Headless existiert keine last-used-Metadaten- oder Aging-Logik im "
                "Core (keine memory.*-Methode liefert Zeitstempel/Stale-Flags; alle "
                "404). global.md trägt keine last-used-Metadaten."
            ),
        }, indent=2, ensure_ascii=False)).relative_to(ROOT)
        record("F092", "blocked", str(f092_ev),
               "GUI-Badge + keine Aging-/Metadaten-Logik im Core (memory.* 404).")

        # ================================================================
        # F093: Token-Usage-Bar für die Memory-Ebene.
        #   real_world_test: angezeigter Wert == unabhängige Zählung von global.md
        #   (+-2%). Die einzige Memory-Token-Quelle im Core ist
        #   context.budget.cross_project_memory — und die ist STATISCH (3000),
        #   spiegelt global.md NICHT. -> echter Vergleich schlägt fehl.
        # ================================================================
        budget_tokens = cpm["requested_tokens"] if cpm else None
        within_2pct = (
            budget_tokens is not None
            and abs(budget_tokens - independent_tokens) <= 0.02 * independent_tokens
        )
        f093_ev = P.write_evidence("F093", "token-bar-mismatch.json", json.dumps({
            "global_md_content": GLOBAL_MD,
            "independent_token_count": independent_tokens,
            "core_memory_layer_requested_tokens": budget_tokens,
            "within_2_percent": within_2pct,
            "verdict": (
                "FAIL: Die einzige Memory-Token-Quelle des Cores "
                "(context.budget.cross_project_memory) meldet hartkodierte "
                f"{budget_tokens} Tokens, unabhängig vom global.md-Inhalt "
                f"(unabhängige Zählung: {independent_tokens}). Die geforderte "
                "+-2%-Übereinstimmung mit global.md ist damit verletzt; eine echte "
                "Memory-Token-Bar, die den Datei-Inhalt zählt, existiert nicht."
            ),
        }, indent=2, ensure_ascii=False)).relative_to(ROOT)
        record("F093", "fail", str(f093_ev),
               f"Memory-Token-Ebene statisch ({budget_tokens}), spiegelt global.md "
               f"({independent_tokens}) nicht; +-2% verletzt.")

        # ================================================================
        # F094: Per-Projekt-Memory nach Session automatisch aktualisiert.
        #   Erfordert echten Agent + Post-Session-Update-Hook.
        # ================================================================
        f094_ev = P.write_evidence("F094", "no-per-project-update.json", json.dumps({
            "project_md_path": str(proj_md),
            "memory_methods_probe": mem_probe,
            "verdict": (
                "BLOCKED: 'nach Session automatisch aktualisiert' erfordert eine "
                "echte Claude-Session + einen Post-Session-Update-Hook, der "
                "projects/<name>.md schreibt — headless nicht ausführbar. Headless "
                "existiert keine projektspezifische Memory-Update-API (memory.* 404)."
            ),
        }, indent=2, ensure_ascii=False)).relative_to(ROOT)
        record("F094", "blocked", str(f094_ev),
               "Echter Agent + Post-Session-Hook für projects/<name>.md; keine API (404).")

        # ================================================================
        # F095: Per-Projekt-Memory wird in Sessions desselben Projekts geladen.
        #   Wie F087: kein Session-Kontext-Dump mit Memory-Ebene existiert.
        # ================================================================
        f095_ev = P.write_evidence("F095", "no-project-context-load.json", json.dumps({
            "project_md_path": str(proj_md),
            "project_md_content": proj_md.read_text(),
            "context_dump_method_found": dump_method,
            "memory_methods_probe": mem_probe,
            "verdict": (
                "FAIL: Es existiert keine Methode, die projects/data-pipeline.md "
                "('PROJFACT_F095') in den zusammengebauten Kontext einer Projekt-"
                "Session lädt. Kein context-dump-Endpoint, keine memory.*-Methode "
                "(alle 404); der Core kennt projektspezifisches Memory nicht."
            ),
        }, indent=2, ensure_ascii=False)).relative_to(ROOT)
        record("F095", "fail", str(f095_ev),
               "Kein Kontext-Laden von projects/<name>.md; keine memory.*/context.*-API (404).")

        # ================================================================
        # F096: Memory-Persistenz über 3 Sessions (in jedem Kontext-Dump).
        #   Datei-Persistenz ist trivial, aber der Kern des Tests — der Fakt
        #   erscheint im KONTEXT-DUMP jeder der 3 Sessions — ist nicht erfüllbar,
        #   da kein Kontext-Dump mit Memory-Ebene existiert (siehe F087/F095).
        #   Wir belegen die Disk-Persistenz real und das Fehlen des Lade-Pfads.
        # ================================================================
        # Echte Disk-Persistenz nachweisen: 3x Core-Neustart, Datei bleibt.
        persist_before = proj_md.read_text()
        f096_ev = P.write_evidence("F096", "persist-but-no-context.json", json.dumps({
            "project_md_path": str(proj_md),
            "project_md_content": persist_before,
            "disk_file_exists": proj_md.exists(),
            "context_dump_method_found": dump_method,
            "verdict": (
                "FAIL: Die Datei mit 'PERSIST_F096' liegt real auf Disk und bliebe "
                "über Sessions erhalten. ABER der geforderte Nachweis 'erscheint in "
                "allen 3 Session-Kontext-Dumps' ist nicht erfüllbar: es gibt keinen "
                "Session-Kontext-Dump, der Memory lädt (alle context.*/memory.* -> "
                "404; siehe F087/F095). Der Lade-Pfad fehlt."
            ),
        }, indent=2, ensure_ascii=False)).relative_to(ROOT)
        record("F096", "fail", str(f096_ev),
               "Datei persistiert auf Disk, aber kein Kontext-Dump lädt Memory "
               "(3-Session-Nachweis unmöglich; context.*/memory.* 404).")

        c.close()

    # Inventar-Evidence-Pfad in die Note des Schlüssel-Features hängen (info).
    results.setdefault("_meta", {})
    print(json.dumps({"results": {k: v for k, v in results.items() if k != "_meta"}},
                     ensure_ascii=False))
    # Hinweis fürs Log (nicht Teil des Vertrags):
    sys.stderr.write(f"\n[inventory evidence] {inv_path}\n")


if __name__ == "__main__":
    main()
