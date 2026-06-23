#!/usr/bin/env python3
"""Echte Verifikation der Session-Archiv-Features (F151–F165).

Jeder Check fuehrt eine reale Operation gegen den echten Rust-Core, die echte
on-disk sessions.db und echtes git aus. Kein Mock, kein erfundenes Ergebnis.
Evidence landet unter test-harness/evidence/<FID>/.

WICHTIGE SUBSTRAT-REALITAET (gegen die echten Quellen verifiziert):
- Der Core stellt nur diese Session-IPC-Methoden bereit: session.create,
  session.list, session.get, session.messages, session.search, session.stats,
  session.stop. Es gibt KEINE IPC-Methode, um Transcript-Zeilen, Tool-Calls,
  File-Diffs oder Events von aussen einzuspeisen — diese schreibt ausschliesslich
  der Live-Claude-Forwarder intern (record_message / record_tool_call /
  record_run_event ...).
- Wir testen daher den REALEN DB-Schreibpfad zweistufig: (1) wir schreiben mit
  exakt denselben SQL-INSERTs wie der Core (append_message/append_file_diff/
  append_event aus cs-sessions/src/lib.rs) direkt in die ECHTE sessions.db, die
  der ECHTE Core angelegt hat, und (2) lesen das Ergebnis ueber die ECHTEN
  IPC-Handler (session.messages/search/stats/list) sowie per direkter SQL wieder
  aus. Damit ist jede Assertion gegen echte Core-Schema- und Query-Logik geprueft.
- Spalten fuer Token-Aufschluesselung, USD-Kosten und Privacy/Verschluesselung
  existieren im Schema NICHT (cs-sessions/src/lib.rs init_schema). Features, die
  diese Persistenz verlangen, sind im Core nicht implementiert -> ehrlich "fail".
- Permission/Hook/MCP-Events und nummerierte Error/Retry-Eintraege werden vom
  Live-Forwarder nicht in die events-Tabelle geschrieben (nur "started",
  "spawn_failed", "completed") -> betroffene Features ehrlich "fail"/"blocked".
- Reine GUI-Interaktionen (Pfeiltasten-Replay, gruen/rot-Diff-Rendering,
  "Prompt wiederholen"-Button, Statistik-Chart-Screenshot) sind headless nicht
  klickbar -> "blocked" mit Grund, wo kein DB/IPC-Substrat existiert.

Aufruf:  python3 test-harness/probes/session_archive.py
Ausgabe: JSON-Zeile  {"results": {fid: {"status": "...", "evidence": "...", "note": "..."}}}
"""
from __future__ import annotations
import json, os, sqlite3, subprocess, sys, time, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# DB-Schreib-Helfer: exakt dieselben INSERTs wie der echte Core
# (cs-sessions/src/lib.rs). Schreibt in die ECHTE sessions.db des laufenden Core.
# ---------------------------------------------------------------------------
def db_append_message(con, session_id, role, content, ts):
    mid = str(uuid.uuid4())
    con.execute(
        "INSERT INTO messages (id, session_id, role, content, created_at) VALUES (?,?,?,?,?)",
        (mid, session_id, role, content, ts),
    )
    con.execute(
        "INSERT INTO transcript_fts (session_id, source, body) VALUES (?, 'message', ?)",
        (session_id, content),
    )
    con.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (ts, session_id))
    return mid


def db_append_tool_call(con, session_id, tool, inp, out, success, ts):
    tid = str(uuid.uuid4())
    con.execute(
        "INSERT INTO tool_calls (id, session_id, message_id, tool_use_id, tool_name, input, output, success, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (tid, session_id, None, str(uuid.uuid4()), tool, json.dumps(inp),
         json.dumps(out) if out is not None else None, 1 if success else 0, ts),
    )
    con.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (ts, session_id))
    return tid


def db_append_file_diff(con, session_id, path, diff, adds, dels, ts):
    did = str(uuid.uuid4())
    con.execute(
        "INSERT INTO file_diffs (id, session_id, path, diff, additions, deletions, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (did, session_id, path, diff, adds, dels, ts),
    )
    con.execute(
        "INSERT INTO transcript_fts (session_id, source, body) VALUES (?, 'file_diff', ?)",
        (session_id, diff),
    )
    con.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (ts, session_id))
    return did


def db_append_event(con, session_id, kind, payload, ts):
    eid = str(uuid.uuid4())
    con.execute(
        "INSERT INTO events (id, session_id, kind, payload, created_at) VALUES (?,?,?,?,?)",
        (eid, session_id, kind, json.dumps(payload) if payload is not None else None, ts),
    )
    con.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (ts, session_id))
    return eid


def schema_columns(con, table) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}


def main():
    log = ROOT / "test-harness/evidence/_session-archive-core.log"
    with P.running_core(library_dir=ROOT, log_path=log) as ctx:
        home, sock = ctx["home"], ctx["sock"]
        c = P.Client(sock)
        db_path = home / ".claudestudio/sessions.db"
        assert db_path.exists(), "sessions.db wurde vom Core nicht angelegt"

        # Eine zweite Connection auf die ECHTE DB des laufenden Core. WAL-Mode ist
        # an, also sehen Core-IPC-Reads unsere Commits sofort.
        con = sqlite3.connect(str(db_path))
        con.isolation_level = None  # autocommit

        # =================================================================
        # F151 — vollstaendiger Transcript persistiert, korrekte Reihenfolge
        # =================================================================
        try:
            # Echte Session ueber den echten IPC-Handler anlegen.
            s = c.request("session.create", {"title": "todo-api multi-step", "cwd": str(ROOT)})
            sid = s["id"]
            # Eine mehrschrittige Session mit mehreren Tool-Calls "durchlaufen":
            # user-msg, assistant-msg, Tool-Call+Output, assistant-msg ...
            base = now_ms()
            timeline = []
            db_append_message(con, sid, "user", "Bitte fuege einen /health Endpoint hinzu", base + 0)
            timeline.append(("message", "user"))
            tc1 = db_append_tool_call(con, sid, "Bash", {"command": "ls"}, {"stdout": "app.py"}, True, base + 1)
            timeline.append(("tool_call", "Bash"))
            db_append_message(con, sid, "assistant", "Ich lese die Datei app.py", base + 2)
            timeline.append(("message", "assistant"))
            tc2 = db_append_tool_call(con, sid, "Edit", {"path": "app.py"}, {"ok": True}, True, base + 3)
            timeline.append(("tool_call", "Edit"))
            db_append_message(con, sid, "assistant", "Der /health Endpoint ist hinzugefuegt", base + 4)
            timeline.append(("message", "assistant"))

            # (1) ueber den ECHTEN IPC-Handler session.messages auslesen
            msgs = c.request("session.messages", {"id": sid})["messages"]
            # (2) direkte SQL: alle Transcript-Zeilen chronologisch
            rows = con.execute(
                "SELECT created_at, 'message' AS k, role AS label FROM messages WHERE session_id=?1 "
                "UNION ALL SELECT created_at, 'tool_call', tool_name FROM tool_calls WHERE session_id=?1 "
                "ORDER BY created_at ASC", (sid,)).fetchall()
            kinds = [(r[1], r[2]) for r in rows]
            # Assertion: genau ein Eintrag je UI-Aktion, korrekte chronologische Reihenfolge
            assert len(msgs) == 3, f"erwartet 3 messages ueber IPC, bekam {len(msgs)}"
            assert kinds == timeline, f"Reihenfolge/Anzahl falsch:\n got={kinds}\n exp={timeline}"
            roles_via_ipc = [(m["role"], m["content"]) for m in msgs]
            e = ev("F151", "transcript.json", json.dumps({
                "session_id": sid,
                "ipc_session_messages": roles_via_ipc,
                "sql_chronological_timeline": kinds,
                "expected_timeline": timeline,
                "assertion": "jede message+tool_call hat genau einen DB-Eintrag in chronologischer Reihenfolge",
            }, ensure_ascii=False, indent=2))
            record("F151", "pass", e, f"{len(kinds)} Eintraege, Reihenfolge korrekt (IPC+SQL)")
        except Exception as e:
            record("F151", "fail", note=str(e))

        # =================================================================
        # F152 — File-Diff im git-Patch-Format gespeichert, git apply --check ok
        # =================================================================
        try:
            # Echtes temporaeres git-Repo (landing-page) anlegen.
            work = home / "landing-page"
            work.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q"], cwd=work, check=True)
            subprocess.run(["git", "config", "user.email", "p@e.de"], cwd=work, check=True)
            subprocess.run(["git", "config", "user.name", "probe"], cwd=work, check=True)
            idx = work / "index.html"
            idx.write_text("<html>\n<body>\nHello\n</body>\n</html>\n")
            subprocess.run(["git", "add", "index.html"], cwd=work, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=work, check=True)
            # Aenderung vornehmen und echten unified-diff erzeugen.
            idx.write_text("<html>\n<body>\nHello, World!\n</body>\n</html>\n")
            diff_text = subprocess.run(["git", "diff", "index.html"], cwd=work,
                                       capture_output=True, text=True, check=True).stdout
            adds = sum(1 for ln in diff_text.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
            dels = sum(1 for ln in diff_text.splitlines() if ln.startswith("-") and not ln.startswith("---"))
            # Diff im Core-Schema speichern (echter file_diffs-INSERT-Pfad).
            s = c.request("session.create", {"title": "landing-page edit", "cwd": str(work)})
            sid = s["id"]
            did = db_append_file_diff(con, sid, "index.html", diff_text, adds, dels, now_ms())
            # Patch wieder aus der DB lesen und gegen den Ausgangszustand checken.
            stored = con.execute("SELECT diff, additions, deletions FROM file_diffs WHERE id=?",
                                  (did,)).fetchone()
            # Repo auf Ausgangszustand zuruecksetzen, dann git apply --check gegen gespeicherten Patch.
            subprocess.run(["git", "checkout", "--", "index.html"], cwd=work, check=True)
            patch_file = home / "stored.patch"
            patch_file.write_text(stored[0])
            chk = subprocess.run(["git", "apply", "--check", str(patch_file)], cwd=work,
                                 capture_output=True, text=True)
            assert chk.returncode == 0, f"git apply --check schlug fehl: {chk.stderr}"
            assert stored[0].startswith("diff --git"), "gespeicherter Diff ist kein unified-diff"
            e = ev("F152", "filediff.txt",
                   f"session_id={sid}\nstored additions={stored[1]} deletions={stored[2]}\n"
                   f"git apply --check rc={chk.returncode} (akzeptiert)\n\n"
                   f"--- gespeicherter Patch aus sessions.db ---\n{stored[0]}")
            record("F152", "pass", e, "Patch gueltig, git apply --check akzeptiert ihn (rc=0)")
        except Exception as e:
            record("F152", "fail", note=str(e))

        # =================================================================
        # F153 — Token-Aufschluesselung (input/output/cache) gespeichert
        # =================================================================
        try:
            cols = schema_columns(con, "sessions")
            mcols = schema_columns(con, "messages")
            token_cols = [c2 for c2 in (cols | mcols) if "token" in c2.lower() or "cache" in c2.lower()]
            # Es existiert KEINE Token-Spalte im Schema -> Persistenz nicht implementiert.
            assert token_cols, (
                "Keine input/output/cache-Token-Spalten im sessions/messages-Schema vorhanden "
                f"(sessions cols={sorted(cols)}, messages cols={sorted(mcols)}). "
                "Token-Aufschluesselung wird vom Core nicht persistiert.")
            # (unerreichbar, solange Schema keine Token-Spalten hat)
            record("F153", "pass", "", "Token-Spalten vorhanden")
        except AssertionError as e:
            ev("F153", "no-token-columns.txt",
               "PRAGMA table_info(sessions) -> " + str(sorted(schema_columns(con, "sessions"))) +
               "\nPRAGMA table_info(messages) -> " + str(sorted(schema_columns(con, "messages"))) +
               "\n\n" + str(e))
            record("F153", "fail",
                   "test-harness/evidence/F153/no-token-columns.txt",
                   "Core-Schema hat keine Token-/Cache-Spalten; Aufschluesselung nicht persistiert")
        except Exception as e:
            record("F153", "fail", note=str(e))

        # =================================================================
        # F154 — USD-Kosten je Session persistiert + im Archiv abrufbar
        # =================================================================
        try:
            cols = schema_columns(con, "sessions")
            cost_cols = [c2 for c2 in cols if "cost" in c2.lower() or "usd" in c2.lower() or "price" in c2.lower()]
            assert cost_cols, (
                f"Keine USD/cost-Spalte in sessions (cols={sorted(cols)}). "
                "Der session.get/list-Handler liefert ebenfalls kein cost-Feld zurueck. "
                "USD-Kosten werden vom Core nicht persistiert.")
            record("F154", "pass", "", "cost-Spalte vorhanden")
        except AssertionError as e:
            # Zusaetzlich belegen: session.get liefert kein cost-Feld
            s = c.request("session.create", {"title": "cost probe", "cwd": str(ROOT)})
            got = c.request("session.get", {"id": s["id"]})
            ev("F154", "no-cost-column.txt",
               "PRAGMA table_info(sessions) -> " + str(sorted(schema_columns(con, "sessions"))) +
               "\nsession.get-Felder -> " + str(sorted(got.keys())) +
               "\n\n" + str(e))
            record("F154", "fail",
                   "test-harness/evidence/F154/no-cost-column.txt",
                   "Core-Schema/IPC haben kein USD-Kostenfeld; Kosten nicht persistiert")
        except Exception as e:
            record("F154", "fail", note=str(e))

        # =================================================================
        # F155 — Permission/Hook/MCP-Events vollstaendig gespeichert
        # =================================================================
        # Substrat: events-Tabelle (kind+payload) existiert und kann solche
        # Eintraege halten. ABER der Live-Forwarder schreibt nur "started"/
        # "spawn_failed"/"completed" — permission/hook/mcp werden NICHT
        # aufgezeichnet. Die echte End-to-End-Aufzeichnung braucht einen echten
        # Claude-Agent im 'ask'-Modus + echten MCP-Tool-Call (headless nicht
        # verfuegbar). Wir verifizieren ehrlich nur das Schema-Substrat und
        # markieren die Funktion als blocked.
        try:
            s = c.request("session.create", {"title": "events substrate", "cwd": str(ROOT)})
            sid = s["id"]
            base = now_ms()
            db_append_event(con, sid, "permission", {"tool": "Bash", "decision": "allow"}, base + 0)
            db_append_event(con, sid, "hook", {"event": "PostToolUse", "matcher": "Edit"}, base + 1)
            db_append_event(con, sid, "mcp", {"server": "fs", "tool": "read_file"}, base + 2)
            rows = con.execute(
                "SELECT kind, payload, created_at FROM events WHERE session_id=? ORDER BY created_at",
                (sid,)).fetchall()
            kinds = [r[0] for r in rows]
            # events-Tabelle haelt die Eintraege mit Zeitstempel — Schema taugt.
            schema_ok = kinds == ["permission", "hook", "mcp"] and all(r[2] for r in rows)
            # Aber: kein Code-Pfad zeichnet diese Event-Arten aus dem Live-Run auf.
            recorded_kinds = subprocess.run(
                ["grep", "-rho", r"record_run_event([^;]*", str(ROOT / "core/crates/cs-cli/src")],
                capture_output=True, text=True).stdout
            assert schema_ok, f"events-Schema haelt die Eintraege nicht: {kinds}"
            e = ev("F155", "events-substrate.txt",
                   "events-Tabelle haelt permission/hook/mcp mit Zeitstempel:\n" +
                   json.dumps([{"kind": r[0], "payload": json.loads(r[1]), "ts": r[2]} for r in rows],
                              ensure_ascii=False, indent=2) +
                   "\n\nABER der Live-Forwarder ruft record_run_event nur fuer:\n" + recorded_kinds +
                   "\n=> permission/hook/mcp werden im echten Run NICHT aufgezeichnet.\n"
                   "Ende-zu-Ende-Nachweis braucht echten Claude-Agent (ask-Modus) + echten MCP-Call.")
            record("F155", "blocked", e,
                   "events-Schema taugt, aber Live-Forwarder zeichnet permission/hook/mcp nicht auf; "
                   "E2E braucht echten Agent+MCP (headless n/v)")
        except Exception as e:
            record("F155", "fail", note=str(e))

        # =================================================================
        # F156 — Fehler und Retries mit Fehlertext, exit-code, nummeriert
        # =================================================================
        try:
            cols = schema_columns(con, "events")
            tcols = schema_columns(con, "tool_calls")
            # Fehlertext laesst sich im events.payload / tool_calls.output ablegen,
            # aber es gibt KEINE exit_code-Spalte und KEINE retry-Nummerierungsspalte.
            has_exit = any("exit" in c2.lower() for c2 in (cols | tcols))
            has_retry = any("retry" in c2.lower() or "attempt" in c2.lower() for c2 in (cols | tcols))
            assert has_exit and has_retry, (
                f"Keine exit_code-/retry-Nummerierungsspalte (events cols={sorted(cols)}, "
                f"tool_calls cols={sorted(tcols)}). Zudem zeichnet der Live-Forwarder weder "
                "Fehler-exit-codes noch nummerierte Retry-Versuche auf.")
            record("F156", "pass", "", "exit_code/retry-Spalten vorhanden")
        except AssertionError as e:
            ev("F156", "no-error-retry-schema.txt",
               "PRAGMA table_info(events) cols -> " + str(sorted(schema_columns(con, "events"))) +
               "\nPRAGMA table_info(tool_calls) cols -> " + str(sorted(schema_columns(con, "tool_calls"))) +
               "\n\nLive-Forwarder record_run_event-Kinds: nur started/spawn_failed/completed.\n"
               "Kein exit-code-Feld, keine nummerierten Retry-Eintraege.\n\n" + str(e))
            record("F156", "fail",
                   "test-harness/evidence/F156/no-error-retry-schema.txt",
                   "Kein exit_code-/Retry-Nummerierungs-Schema; Fehler/Retry nicht strukturiert persistiert")
        except Exception as e:
            record("F156", "fail", note=str(e))

        # =================================================================
        # F157 — Listenansicht chronologisch, neueste oben
        # =================================================================
        try:
            # Drei Sessions nacheinander mit klar steigenden created_at anlegen.
            # session.create setzt created_at=now_millis(); um deterministische
            # Reihenfolge zu garantieren, setzen wir created_at gezielt per SQL.
            ids = []
            t0 = now_ms()
            for i, (title, cwd) in enumerate([
                ("todo-api run A", str(ROOT)),
                ("landing-page run B", str(ROOT)),
                ("todo-api run C", str(ROOT)),
            ]):
                r = c.request("session.create", {"title": title, "cwd": cwd})
                created = t0 + i * 1000
                con.execute("UPDATE sessions SET created_at=?, updated_at=? WHERE id=?",
                            (created, created, r["id"]))
                ids.append((r["id"], title, created))
            # ECHTER IPC-Handler session.list (ORDER BY created_at DESC).
            listed = c.request("session.list", {"limit": 100})["sessions"]
            our = [s2 for s2 in listed if s2["id"] in {i[0] for i in ids}]
            order_ids = [s2["id"] for s2 in our]
            # Erwartete absteigende Reihenfolge: run C, run B, run A.
            expected = [ids[2][0], ids[1][0], ids[0][0]]
            assert order_ids == expected, f"Reihenfolge falsch: got={order_ids} exp={expected}"
            # streng absteigende created_at?
            cas = [s2["created_at"] for s2 in our]
            assert cas == sorted(cas, reverse=True), f"created_at nicht streng absteigend: {cas}"
            e = ev("F157", "list-order.json", json.dumps({
                "created_in_order": [{"id": i[0], "title": i[1], "created_at": i[2]} for i in ids],
                "session_list_returned": [{"id": s2["id"], "title": s2["title"],
                                           "created_at": s2["created_at"]} for s2 in our],
                "expected_desc_ids": expected,
                "assertion": "neueste zuerst, streng absteigend nach created_at",
            }, ensure_ascii=False, indent=2))
            record("F157", "pass", e, "session.list liefert neueste-zuerst (streng absteigend)")
        except Exception as e:
            record("F157", "fail", note=str(e))

        # =================================================================
        # F158 — Archiv-Filter (Projekt/Agent/Zeitraum/Modell/Kosten/Tools)
        # =================================================================
        # Es gibt keinen Filter-IPC-Handler im Core (session.list nimmt nur
        # limit/offset). Kosten-Filter ist unmoeglich (keine cost-Spalte, s. F154).
        # Die UI-Filter laufen rein clientseitig ueber Swift-Sample-Daten
        # (ArchiveView nutzt ArchivedSession.samples), nicht ueber die DB.
        # Substrat-Teilnachweis: projekt(cwd)- und modell-basierte SQL-Filter
        # liefern korrekte Teilmengen — das beweisen wir gegen die echte DB.
        try:
            cwd_lp = str(home / "landing-page")
            ids = []
            specs = [
                ("F158 lp opus", cwd_lp, "opus"),
                ("F158 todo sonnet", str(ROOT), "sonnet"),
                ("F158 lp sonnet", cwd_lp, "sonnet"),
            ]
            for title, cwd, model in specs:
                r = c.request("session.create", {"title": title, "cwd": cwd, "model": model})
                ids.append(r["id"])
            our = set(ids)
            # SQL-Gegenquery: Projekt=landing-page
            by_proj = {row[0] for row in con.execute(
                "SELECT id FROM sessions WHERE cwd=? AND id IN ({})".format(
                    ",".join("?" * len(ids))), (cwd_lp, *ids)).fetchall()}
            # SQL-Gegenquery: Modell=sonnet
            by_model = {row[0] for row in con.execute(
                "SELECT id FROM sessions WHERE model=? AND id IN ({})".format(
                    ",".join("?" * len(ids))), ("sonnet", *ids)).fetchall()}
            assert by_proj == {ids[0], ids[2]}, f"Projekt-Filter falsch: {by_proj}"
            assert by_model == {ids[1], ids[2]}, f"Modell-Filter falsch: {by_model}"
            # Aber: kein Kosten-Filter moeglich (keine Spalte), kein Filter-IPC-Handler.
            cost_possible = "cost" in {c2.lower() for c2 in schema_columns(con, "sessions")} or \
                            "cost_usd" in schema_columns(con, "sessions")
            has_filter_ipc = "session.filter" in (ROOT / "core/crates/cs-cli/src/router.rs").read_text()
            e = ev("F158", "filter-substrate.txt",
                   f"Projekt(cwd)-Filter SQL -> {sorted(by_proj)} (== erwartet)\n"
                   f"Modell-Filter SQL      -> {sorted(by_model)} (== erwartet)\n"
                   f"Kosten-Filter moeglich? {cost_possible} (keine cost-Spalte)\n"
                   f"session.filter IPC-Handler vorhanden? {has_filter_ipc}\n\n"
                   "Projekt/Modell-Filter sind ueber die DB korrekt moeglich, ABER es gibt keinen\n"
                   "Filter-IPC-Handler; die UI filtert clientseitig ueber Swift-Sample-Daten, und\n"
                   "Kosten-/Tools-Filter haben kein DB-Substrat. Voller Feature-Nachweis braucht GUI.")
            record("F158", "blocked", e,
                   "Projekt/Modell-Filter per SQL korrekt, aber kein Filter-IPC + kein Kosten-Substrat; "
                   "UI filtert ueber Sample-Daten -> GUI noetig")
        except Exception as e:
            record("F158", "fail", note=str(e))

        # =================================================================
        # F159 — FTS5-Volltextsuche < 100ms, korrekter Treffer bei vielen Sessions
        # =================================================================
        try:
            needle = "Healthcheck-Endpoint"
            target_sid = None
            for i in range(60):  # >= 50 Sessions mit variierenden Inhalten
                r = c.request("session.create", {"title": f"bulk session {i}", "cwd": str(ROOT)})
                sid = r["id"]
                ts = now_ms() + i
                if i == 37:
                    target_sid = sid
                    db_append_message(con, sid, "user",
                                      f"Bitte implementiere den {needle} fuer das todo-api Projekt", ts)
                else:
                    db_append_message(con, sid, "user",
                                      f"Zufaelliger Inhalt Nummer {i} ohne den gesuchten Begriff", ts)
            total = con.execute("SELECT count(*) FROM sessions").fetchone()[0]
            assert total >= 50, f"weniger als 50 Sessions: {total}"
            # Direkte FTS5-Gegenquery (Substrat) + Zeitmessung. FTS5 deutet den
            # Bindestrich als Token-Trenner und 'Endpoint' sonst als Spaltennamen —
            # gequotet als Phrase findet das rohe FTS5-Substrat die Ziel-Session.
            fts_query = '"' + needle.replace('"', '""') + '"'
            t1 = time.perf_counter()
            hits = con.execute(
                "SELECT session_id FROM transcript_fts WHERE transcript_fts MATCH ?",
                (fts_query,)).fetchall()
            elapsed_ms = (time.perf_counter() - t1) * 1000.0
            hit_ids = {h[0] for h in hits}
            substrate_ok = hit_ids == {target_sid} and elapsed_ms < 100.0

            # ECHTER Archiv-Suchpfad: der session.search-IPC-Handler ist das, was die
            # Archivansicht tatsaechlich aufruft. Genau hier liegt ein realer Core-Bug:
            # der Handler reicht den Such-String ungesaeubert in FTS5 MATCH (Semantik
            # liefert nichts -> FTS-Fallback), sodass der Bindestrich-Begriff einen
            # 'no such column'-Fehler wirft. Wir fangen den ECHTEN Fehler als Evidence.
            ipc_error = None
            ipc_hit_sessions = []
            try:
                ipc = c.request("session.search", {"query": needle, "limit": 8})
                ipc_hit_sessions = sorted({h["session_id"] for h in ipc.get("hits", [])})
            except P.RemoteError as re:
                ipc_error = str(re)

            e = ev("F159", "fts-search.json", json.dumps({
                "total_sessions": total,
                "needle": needle,
                "target_session": target_sid,
                "direct_fts5_match_session_ids": sorted(hit_ids),
                "direct_fts5_query_ms": round(elapsed_ms, 3),
                "direct_fts5_under_100ms": elapsed_ms < 100.0,
                "direct_fts5_substrate_ok": substrate_ok,
                "ipc_session_search_query": needle,
                "ipc_session_search_error": ipc_error,
                "ipc_session_search_hit_sessions": ipc_hit_sessions,
                "verdict": "Archiv-Suchhandler (session.search) wirft echten Core-Fehler bei "
                           "'Healthcheck-Endpoint' -> Feature-Test schlaegt am echten IPC-Pfad fehl",
            }, ensure_ascii=False, indent=2))

            if ipc_error is not None:
                # Der vom Feature verlangte Archiv-Suchweg schlaegt real fehl.
                record("F159", "fail", e,
                       f"session.search wirft realen Core-Fehler bei '{needle}': {ipc_error} "
                       f"(direktes FTS5-Substrat findet Ziel in {elapsed_ms:.2f}ms, aber der "
                       "Archiv-IPC-Handler saeubert den Query nicht)")
            elif substrate_ok and ipc_hit_sessions == [target_sid]:
                record("F159", "pass", e,
                       f"{total} Sessions, Treffer exakt 1 korrekt via IPC, {elapsed_ms:.2f}ms < 100ms")
            else:
                record("F159", "fail", e,
                       f"IPC-Treffer {ipc_hit_sessions} != [{target_sid}] (substrate_ok={substrate_ok})")
        except Exception as e:
            record("F159", "fail", note=str(e))

        # =================================================================
        # F160 — Session-Replay Step-Through (Pfeiltasten vor/zurueck)
        # =================================================================
        # Substrat: session.messages liefert geordnete Schritte (Replay-Quelle).
        # Aber die Pfeiltasten-Navigation ist reine SwiftUI-Interaktion -> headless
        # nicht klickbar. Wir belegen nur, dass die geordnete Schrittquelle existiert.
        try:
            r = c.request("session.create", {"title": "replay source", "cwd": str(ROOT)})
            sid = r["id"]
            base = now_ms()
            steps = ["Schritt 1: Plan", "Schritt 2: Implementierung", "Schritt 3: Test", "Schritt 4: Fertig"]
            for i, txt in enumerate(steps):
                db_append_message(con, sid, "assistant" if i % 2 else "user", txt, base + i)
            msgs = c.request("session.messages", {"id": sid})["messages"]
            ordered = [m["content"] for m in msgs]
            assert ordered == steps, f"Replay-Schrittquelle nicht geordnet: {ordered}"
            e = ev("F160", "replay-source.json", json.dumps({
                "session_id": sid,
                "ordered_steps_via_ipc": ordered,
                "note": "geordnete Schrittquelle vorhanden; Pfeiltasten-Navigation ist SwiftUI-GUI",
            }, ensure_ascii=False, indent=2))
            record("F160", "blocked", e,
                   "Geordnete Replay-Schrittquelle per IPC vorhanden, aber Pfeiltasten-Step-Through "
                   "ist SwiftUI-Interaktion (headless nicht klickbar)")
        except Exception as e:
            record("F160", "fail", note=str(e))

        # =================================================================
        # F161 — Diff in Archivansicht gruen/rot eingebettet
        # =================================================================
        # Substrat: file_diffs.diff haelt einen gueltigen unified-diff (F152).
        # Die gruen/rot-Hervorhebung ist reines SwiftUI-Rendering -> Screenshot
        # noetig. Wir belegen, dass die Diff-Quelle add/remove-Zeilen enthaelt.
        try:
            row = con.execute(
                "SELECT diff FROM file_diffs ORDER BY created_at DESC LIMIT 1").fetchone()
            assert row, "kein gespeicherter Diff fuer die Detailansicht vorhanden"
            diff_text = row[0]
            add_lines = [l for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++")]
            del_lines = [l for l in diff_text.splitlines() if l.startswith("-") and not l.startswith("---")]
            assert add_lines and del_lines, "Diff enthaelt keine add/remove-Zeilen zum Hervorheben"
            e = ev("F161", "diff-source.txt",
                   f"Diff-Quelle (file_diffs) hat {len(add_lines)} add- und {len(del_lines)} remove-Zeilen.\n"
                   f"add(+) Beispiel: {add_lines[0]}\nremove(-) Beispiel: {del_lines[0]}\n\n"
                   "Die gruen/rot-Einfaerbung erfolgt im SwiftUI-Renderer der Detailansicht -> Screenshot noetig.")
            record("F161", "blocked", e,
                   "Diff-Quelle mit add/remove-Zeilen vorhanden, aber gruen/rot-Rendering ist SwiftUI-GUI "
                   "(Screenshot headless n/v)")
        except Exception as e:
            record("F161", "fail", note=str(e))

        # =================================================================
        # F162 — "Diesen Prompt wiederholen" -> neue Session, identischer Prompt
        # =================================================================
        # Substrat: Initial-Prompt einer Session = erste user-message. Wir simulieren
        # das "Wiederholen", indem wir die erste user-message der Quelle in eine neue
        # Session uebernehmen, und vergleichen byte-identisch. Der echte Stream-Lauf
        # braucht einen echten Claude-Agent -> nur Prompt-Identitaet ist headless pruefbar.
        try:
            src = c.request("session.create", {"title": "repeat source", "cwd": str(ROOT)})
            src_id = src["id"]
            initial_prompt = "Implementiere bitte die /users API mit Pagination."
            db_append_message(con, src_id, "user", initial_prompt, now_ms())
            # erste user-message der Quelle holen
            src_msgs = c.request("session.messages", {"id": src_id})["messages"]
            src_initial = next(m["content"] for m in src_msgs if m["role"] == "user")
            # "Wiederholen": neue Session mit demselben Initial-Prompt
            new = c.request("session.create", {"title": "repeat copy", "cwd": str(ROOT)})
            new_id = new["id"]
            db_append_message(con, new_id, "user", src_initial, now_ms())
            new_msgs = c.request("session.messages", {"id": new_id})["messages"]
            new_initial = next(m["content"] for m in new_msgs if m["role"] == "user")
            assert new_initial == src_initial == initial_prompt, "Initial-Prompt nicht byte-identisch"
            assert new_initial.encode() == initial_prompt.encode(), "Byte-Vergleich fehlgeschlagen"
            e = ev("F162", "repeat-prompt.json", json.dumps({
                "source_session": src_id,
                "new_session": new_id,
                "source_initial_prompt": src_initial,
                "new_initial_prompt": new_initial,
                "byte_identical": new_initial.encode() == src_initial.encode(),
                "note": "Prompt-Identitaet bewiesen; der echte laufende Stream braucht einen echten Claude-Agent",
            }, ensure_ascii=False, indent=2))
            record("F162", "blocked", e,
                   "Initial-Prompt-Uebernahme byte-identisch (DB/IPC bewiesen), aber 'neue Session laeuft "
                   "echt (Stream)' braucht echten Claude-Agent + GUI-Button (headless n/v)")
        except Exception as e:
            record("F162", "fail", note=str(e))

        # =================================================================
        # F163 — "Ab hier weitermachen": Transcript-Prefix als Kontext
        # =================================================================
        # Substrat: Prefix bis zum gewaehlten Schritt = erste N messages. Wir belegen,
        # dass der Prefix exakt rekonstruierbar ist. Dass Claudes Antwort den Prefix
        # referenziert, braucht einen echten Agent -> blocked.
        try:
            src = c.request("session.create", {"title": "continue-from source", "cwd": str(ROOT)})
            src_id = src["id"]
            base = now_ms()
            full = [
                ("user", "Lege die Datei config.yaml an"),
                ("assistant", "config.yaml wurde angelegt"),
                ("user", "Setze den Port auf 8080"),
                ("assistant", "Port steht jetzt auf 8080"),
            ]
            for i, (role, txt) in enumerate(full):
                db_append_message(con, src_id, role, txt, base + i)
            # "Ab hier weitermachen" an Schritt index 1 (mittlerer Schritt):
            cutoff_index = 2  # Prefix = erste 2 Schritte
            msgs = c.request("session.messages", {"id": src_id})["messages"]
            prefix = [(m["role"], m["content"]) for m in msgs[:cutoff_index]]
            expected_prefix = full[:cutoff_index]
            assert prefix == expected_prefix, f"Prefix falsch: {prefix} != {expected_prefix}"
            e = ev("F163", "context-prefix.json", json.dumps({
                "source_session": src_id,
                "cutoff_index": cutoff_index,
                "context_prefix": prefix,
                "expected_prefix": expected_prefix,
                "note": "Prefix exakt rekonstruierbar; 'Claude referenziert Prefix' braucht echten Agent",
            }, ensure_ascii=False, indent=2))
            record("F163", "blocked", e,
                   "Transcript-Prefix bis Schritt exakt rekonstruierbar (DB/IPC), aber 'Claude kennt "
                   "uebernommenen Kontext' braucht echten laufenden Agent (headless n/v)")
        except Exception as e:
            record("F163", "fail", note=str(e))

        # =================================================================
        # F164 — Statistik-Ansicht: Gesamtzahl/Token/Kosten + teuerste/laengste
        # =================================================================
        # session.stats liefert Gesamtzahlen (sessions/messages/tool_calls/...),
        # ABER es gibt keine Token- und keine Kosten-Aggregate (keine Spalten,
        # s. F153/F154). "teuerste Session" ist unmoeglich. Wir verifizieren die
        # vorhandenen Aggregate gegen SUM/COUNT-Gegenqueries und legen offen,
        # dass Token-/Kosten-Aggregate fehlen.
        try:
            stats = c.request("session.stats", {})
            sql_sessions = con.execute("SELECT count(*) FROM sessions").fetchone()[0]
            sql_messages = con.execute("SELECT count(*) FROM messages").fetchone()[0]
            sql_tools = con.execute("SELECT count(*) FROM tool_calls").fetchone()[0]
            sql_diffs = con.execute("SELECT count(*) FROM file_diffs").fetchone()[0]
            sql_events = con.execute("SELECT count(*) FROM events").fetchone()[0]
            assert stats["sessions"] == sql_sessions, f"sessions {stats['sessions']} != {sql_sessions}"
            assert stats["messages"] == sql_messages, f"messages {stats['messages']} != {sql_messages}"
            assert stats["tool_calls"] == sql_tools, f"tool_calls {stats['tool_calls']} != {sql_tools}"
            assert stats["file_diffs"] == sql_diffs, f"file_diffs {stats['file_diffs']} != {sql_diffs}"
            assert stats["events"] == sql_events, f"events {stats['events']} != {sql_events}"
            has_tokens = "tokens" in stats or "total_tokens" in stats
            has_cost = "cost" in stats or "cost_usd" in stats or "total_cost" in stats
            # Das Feature verlangt Token- UND Kostenaggregate + teuerste Session -> nicht vorhanden.
            assert has_tokens and has_cost, (
                "session.stats hat keine Token-/Kostenaggregate; 'teuerste Session' nicht ermittelbar "
                f"(stats-Felder={sorted(stats.keys())}).")
            record("F164", "pass", "", "alle Stats-Aggregate inkl. Token/Kosten vorhanden")
        except AssertionError as e:
            extra = ev("F164", "stats-aggregates.json", json.dumps({
                "session_stats_ipc": stats if 'stats' in dir() else None,
                "sql_counts": {"sessions": con.execute("SELECT count(*) FROM sessions").fetchone()[0],
                               "messages": con.execute("SELECT count(*) FROM messages").fetchone()[0],
                               "tool_calls": con.execute("SELECT count(*) FROM tool_calls").fetchone()[0]},
                "missing": "keine Token-/Kostenaggregate, keine 'teuerste/laengste Session'",
                "detail": str(e),
            }, ensure_ascii=False, indent=2))
            record("F164", "fail", extra,
                   "Zaehl-Aggregate korrekt, aber keine Token-/Kostenaggregate -> 'teuerste Session' fehlt")
        except Exception as e:
            record("F164", "fail", note=str(e))

        # =================================================================
        # F165 — Privacy-Modus: AES-256 + gzip + nicht in Standardliste
        # =================================================================
        try:
            cols = schema_columns(con, "sessions")
            mcols = schema_columns(con, "messages")
            priv_cols = [c2 for c2 in (cols | mcols)
                         if any(k in c2.lower() for k in ("private", "encrypt", "cipher", "aes", "blob", "gzip", "compress"))]
            assert priv_cols, (
                f"Keine Privacy-/Verschluesselungs-/Kompressionsspalte (sessions={sorted(cols)}, "
                f"messages={sorted(mcols)}). Transcript wird als Klartext in messages.content gespeichert; "
                "kein AES-256, keine gzip-Kompression, kein private-Flag.")
            record("F165", "pass", "", "Privacy-Spalten vorhanden")
        except AssertionError as e:
            # Belegen: content ist Klartext, kein private-Flag, kein Filter in list_sessions
            ev("F165", "no-privacy.txt",
               "PRAGMA table_info(sessions) -> " + str(sorted(schema_columns(con, "sessions"))) +
               "\nPRAGMA table_info(messages) -> " + str(sorted(schema_columns(con, "messages"))) +
               "\n\nlist_sessions hat keinen private-Filter (SELECT ... ORDER BY created_at DESC).\n"
               "messages.content ist Klartext; kein AES-256, kein gzip, kein private-Flag.\n\n" + str(e))
            record("F165", "fail",
                   "test-harness/evidence/F165/no-privacy.txt",
                   "Kein Privacy-Modus im Core: kein AES-256, kein gzip, kein private-Flag/Filter")
        except Exception as e:
            record("F165", "fail", note=str(e))

        con.close()
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
