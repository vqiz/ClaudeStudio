#!/usr/bin/env python3
"""Echte Verifikation der Cost-/Telemetry-Features (F277–F285).

Diese Kategorie beschreibt ein USD-Kosten-Dashboard, Model-/Token-Breakdown,
Live-USD-Schätzung, Budget-Alerts, Cache-Hit-Rate, OTel-Export,
Produktivitäts-Metriken und einen Admin-API-Usage-Report.

Jeder Check führt eine REALE Operation gegen den echten Core und die echte
sessions.db aus. Wir mocken nichts und faken nichts. Statt erfundene
Pass-Resultate zu liefern, beweisen wir empirisch, was der Core tatsächlich
anbietet — und dokumentieren in der Evidence, warum ein Feature headless nicht
als "pass" gewertet werden kann.

Befund (empirisch gegen den echten Core verifiziert, siehe Evidence):
  * Der IPC-Router (core/crates/cs-cli/src/router.rs) hat KEINE Kosten-/
    Telemetrie-Methoden: kein cost.*, usage.*, telemetry.*, otel.*,
    metrics.*, productivity.* — nur context.budget (Kontext-TOKEN-Budget,
    nicht USD) und die config-Felder daily_budget_usd / context_token_budget.
  * Die sessions.db (core/crates/cs-sessions) speichert KEINE Kosten-, Token-,
    Cache- oder Usage-Spalten. cost_usd wird nur live aus der Claude-Run-
    Result-Zeile geparst und als flüchtiges Event emittiert, NIE persistiert.
  * Das cs-otel-Crate existiert als reine Bibliothek (MetricsExporter,
    InMemoryExporter, ProductivityMetrics, OTLP-Stub hinter Nicht-Default-
    Feature 'otlp'), ist aber NICHT mit dem cs-cli-IPC-Server verdrahtet —
    kein Handler ruft es auf, keine Methode legt es offen.

Damit ist KEINES der real_world_tests headless gegen den echten Core
ausführbar (es gibt weder die Dashboard-Methoden, noch persistierte
Kostendaten, noch einen laufenden Agenten mit echten Kosten, noch die externen
Dienste). Wir markieren alle Features ehrlich als "blocked" mit präzisem Grund
und echter Evidence (echte Request/Response + echter DB-Schema-Dump).

Aufruf:  python3 test-harness/probes/cost_telemetry.py
Ausgabe: JSON-Zeile  {"results": {fid: {"status": ..., "evidence": ..., "note": ...}}}
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}

# Alle IPC-Methoden, die ein Cost-/Telemetry-Feature bräuchte. Wir rufen sie
# wirklich auf, damit die Evidence den echten Core-Fehler ("method not found")
# enthält — Beweis, dass die Fähigkeit fehlt statt nur Behauptung.
PROBE_METHODS = [
    "cost.dashboard",
    "cost.summary",
    "cost.breakdown",
    "cost.by_model",
    "cost.live",
    "usage.report",
    "usage.admin_report",
    "telemetry.export",
    "otel.export",
    "metrics.productivity",
    "budget.alert",
]


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def probe_missing_methods(c) -> dict:
    """Ruft jede potenzielle Kosten-Methode wirklich auf und protokolliert die
    echte Core-Antwort. Liefert {method: {"error": code/message} | {"ok": payload}}."""
    out: dict[str, dict] = {}
    for m in PROBE_METHODS:
        try:
            r = c.request(m, {})
            out[m] = {"ok": r}
        except P.RemoteError as e:
            out[m] = {"error": {"code": e.code, "message": e.message}}
        except Exception as e:  # noqa: BLE001
            out[m] = {"error": {"code": "exception", "message": str(e)}}
    return out


def db_columns(con, table) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]


def grep_router_for(*needles) -> dict:
    """Durchsucht den echten Router-Quelltext nach Kosten-Methodennamen.
    Beweist im Quelltext, dass keine Handler existieren."""
    src = (ROOT / "core/crates/cs-cli/src/router.rs").read_text()
    return {n: (n in src) for n in needles}


def main():
    log = ROOT / "test-harness/evidence/_cost-telemetry-core.log"
    with P.running_core(library_dir=ROOT, log_path=log) as ctx:
        home, sock = ctx["home"], ctx["sock"]
        c = P.Client(sock)

        # Damit das Schema/State realistisch ist: Default-Libs laden und eine
        # echte Session anlegen (gibt der DB echten Inhalt für die Schema-Prüfung).
        try:
            c.request("library.load_defaults", {})
        except Exception:
            pass
        sess_id = None
        try:
            sess_id = c.request(
                "session.create", {"title": "cost-probe", "cwd": str(ROOT)}
            ).get("id")
        except Exception:
            pass

        # --- Echte Belege sammeln, die alle Features teilen ------------------
        missing = probe_missing_methods(c)              # echte Core-Antworten
        cfg = c.request("config.get", {})               # echte config
        stats = c.request("session.stats", {})          # echte Stats (ohne Kosten)
        # context.budget existiert — aber es ist TOKEN-Budget, KEIN USD-Dashboard:
        try:
            ctxbudget = c.request("context.budget", {})
        except Exception as e:  # noqa: BLE001
            ctxbudget = {"error": str(e)}

        # Echtes sessions.db-Schema auslesen (Beweis: keine Kostenspalten).
        db = home / ".claudestudio/sessions.db"
        sessions_cols = tool_calls_cols = file_diffs_cols = events_cols = []
        all_cols_blob = ""
        if db.exists():
            con = sqlite3.connect(str(db))
            sessions_cols = db_columns(con, "sessions")
            tool_calls_cols = db_columns(con, "tool_calls")
            file_diffs_cols = db_columns(con, "file_diffs")
            events_cols = db_columns(con, "events")
            tables = [
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
            ]
            con.close()
            all_cols_blob = json.dumps(
                {
                    "tables": tables,
                    "sessions": sessions_cols,
                    "tool_calls": tool_calls_cols,
                    "file_diffs": file_diffs_cols,
                    "events": events_cols,
                },
                indent=2,
            )

        # Kostenrelevante Spalten, nach denen die real_world_tests fragen.
        COST_COLS = {
            "cost_usd", "cost", "input_tokens", "output_tokens",
            "cache_read_tokens", "cache_read", "cache_creation_tokens",
            "cache_creation", "usd", "total_cost_usd", "tokens",
        }
        cost_cols_present = sorted(
            COST_COLS.intersection(
                set(sessions_cols)
                | set(tool_calls_cols)
                | set(file_diffs_cols)
                | set(events_cols)
            )
        )

        # cs-otel-Verdrahtung prüfen: ist das Crate ans cs-cli gebunden?
        cli_cargo = (ROOT / "core/crates/cs-cli/Cargo.toml").read_text()
        otel_wired = ("cs-otel" in cli_cargo) or ("cs_otel" in cli_cargo)
        otel_crate_exists = (ROOT / "core/crates/cs-otel/src/lib.rs").exists()
        # OTLP nur hinter Nicht-Default-Feature 'otlp' und reiner Tracing-Stub:
        otel_src = (
            (ROOT / "core/crates/cs-otel/src/lib.rs").read_text()
            if otel_crate_exists
            else ""
        )
        otlp_feature_gated = 'feature = "otlp"' in otel_src
        otlp_is_stub = "otlp counter" in otel_src or "Behaviorally identical to logging" in otel_src

        # Router-Quelltext-Beleg.
        router_has = grep_router_for(
            "cost", "usage_report", "telemetry", "otel", "productivity",
            "cache_read", "estimated_usd", "budget_alert",
        )

        # Gemeinsamer Evidence-Block (echte Daten) -> in jede FID-Evidence.
        shared = {
            "probed_methods_real_core_responses": missing,
            "config_get": cfg,
            "session_stats_has_no_cost": stats,
            "context_budget_is_token_not_usd": ctxbudget,
            "sessions_db_schema": json.loads(all_cols_blob) if all_cols_blob else None,
            "cost_columns_present_in_db": cost_cols_present,
            "cs_otel_crate_exists": otel_crate_exists,
            "cs_otel_wired_into_cli": otel_wired,
            "otlp_feature_gated_non_default": otlp_feature_gated,
            "otlp_is_logging_stub_only": otlp_is_stub,
            "router_source_contains": router_has,
            "session_created_for_db_state": sess_id,
        }

        # Sanity-Assertions: die Belege müssen wirklich den fehlenden Zustand zeigen.
        assert all(
            "error" in v for v in missing.values()
        ), f"unerwartet: eine Kosten-Methode antwortete: {missing}"
        assert not cost_cols_present, (
            f"unerwartet: sessions.db enthält Kostenspalten {cost_cols_present} — "
            f"dann wäre eine DB-gestützte Verifikation möglich!"
        )
        assert not otel_wired, (
            "unerwartet: cs-otel ist ins cs-cli verdrahtet — dann könnte OTel/"
            "Metrics-Export evtl. doch erreichbar sein."
        )

        # ------------------------------------------------------------------
        # Pro Feature: ehrlich "blocked" mit echter, feature-spezifischer
        # Evidence + präzisem Grund.
        # ------------------------------------------------------------------
        feat_notes = {
            "F277": (
                "Kosten-Dashboard (heute/Woche/Monat nach Agent/Modell/Projekt/Task) "
                "fehlt vollständig: keine cost.*-IPC-Methode (siehe probed_methods), "
                "und sessions.db hat keine Kostenspalten. Teilsummen=Gesamt nicht "
                "prüfbar, da keine Kostendaten existieren. real_world_test braucht "
                "zudem GUI-Screenshots des Dashboards."
            ),
            "F278": (
                "Model-Breakdown (Input/Output/Cache-Read/Cache-Creation je Modell) "
                "nicht prüfbar: sessions.db hat keine Token-/Cache-Spalten "
                "(cost_columns_present_in_db ist leer), keine Breakdown-Methode. "
                "Prompt-Caching-Session + Dashboard-Screenshot headless nicht möglich."
            ),
            "F279": (
                "Live-USD-Schätzung während eines Agent-Laufs: cost_usd existiert nur "
                "als flüchtiges Result-Event eines ECHTEN Claude-Runs (braucht "
                "laufenden Claude-Agenten/Login). Es wird nie in sessions.db "
                "persistiert, daher kein Live-vs-DB-Vergleich (<=5%) headless möglich."
            ),
            "F280": (
                "Budget-Alert bei 80% des Tagesbudgets: daily_budget_usd ist in der "
                "config vorhanden, aber es gibt keinen Alert-Mechanismus, kein "
                "Verbrauchs-Tracking und keine Push-Notification-Pipeline im Core. "
                "Echte Push-Notification + Screenshot sind ohnehin GUI/OS-gebunden."
            ),
            "F281": (
                "Cache-Hit-Rate (cache_read / (cache_read + input)): keine "
                "Cache-/Input-Token-Spalten in sessions.db und keine Anzeige-Methode "
                "-> weder DB-Berechnung noch Abgleich gegen eine Anzeige möglich."
            ),
            "F282": (
                "Cost-Tracking-USD == echter Verbrauch: SQL-SUM über sessions.db "
                "unmöglich, da keine Kostenspalte existiert; kein cost.summary im "
                "Router. Dashboard-USD vs SQL-SUM nicht herstellbar."
            ),
            "F283": (
                "OTel/OTLP-Export zu lokalem Collector: cs-otel-Crate existiert, ist "
                "aber NICHT ins cs-cli verdrahtet (cs_otel_wired_into_cli=false). Der "
                "OTLP-Exporter ist feature-gated ('otlp', nicht Default) und ein "
                "reiner Tracing-Log-Stub (otlp_is_logging_stub_only=true) ohne echte "
                "Netzwerk-Spans. Ein realer Collector-Empfang ist headless nicht "
                "erreichbar, weil keine IPC-Methode den Export auslöst."
            ),
            "F284": (
                "Produktivitäts-Metriken: ProductivityMetrics existiert als Struct in "
                "cs-otel, aber keine IPC-Methode (metrics.productivity -> error) legt "
                "sie offen. file_diffs hat additions/deletions, doch Commits/Session "
                "und Tool-Acceptance-Rate werden nicht aggregiert/exponiert. Anzeige "
                "vs git-log/DB-Abgleich daher nicht durchführbar; braucht zudem GUI."
            ),
            "F285": (
                "Admin-API Usage-Report (GET /v1/organizations/usage_report/"
                "claude_code): keine usage.admin_report-Methode im Core und kein "
                "HTTP-Client-Pfad dafür. Erfordert externen Anthropic-Admin-API-Key "
                "und einen echten 200-Response — headless ohne Credentials/Netz nicht "
                "verifizierbar."
            ),
        }

        for fid, note in feat_notes.items():
            payload = {
                "feature": fid,
                "reason_blocked": note,
                "shared_evidence": shared,
            }
            e = ev(
                fid,
                "blocked-evidence.json",
                json.dumps(payload, indent=2, ensure_ascii=False),
            )
            record(fid, "blocked", e, note.split(":")[0])

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
