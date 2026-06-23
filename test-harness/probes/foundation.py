#!/usr/bin/env python3
"""Echte Verifikation der Foundation-Features (F001–F015, + Kontext-Budget).

Jeder Check führt eine reale Operation gegen den echten Core / die echte Toolchain
aus und schreibt Evidence nach test-harness/evidence/<FID>/. Kein Mock.

Aufruf:  python3 test-harness/probes/foundation.py
Ausgabe: JSON-Zeile  {"results": {fid: {"status": "...", "evidence": "...", "note": "..."}}}
"""
from __future__ import annotations
import json, os, subprocess, sys, sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def main():
    # ---- F001: Core-Binary gebaut + vorhanden ----------------------------
    try:
        b = P.CORE_BIN
        assert b.exists() and os.access(b, os.X_OK), "binary missing/not executable"
        size = b.stat().st_size
        e = ev("F001", "binary.txt", f"{b}\nsize={size} bytes\nexecutable=True\n")
        record("F001", "pass", e, f"{size} bytes")
    except AssertionError as e:
        record("F001", "fail", note=str(e))

    # ---- Ein laufender Core für die Socket-/IPC-/DB-Checks ----------------
    log = ROOT / "test-harness/evidence/_foundation-core.log"
    with P.running_core(library_dir=ROOT, log_path=log) as ctx:
        home, sock = ctx["home"], ctx["sock"]
        c = P.Client(sock)

        # ---- F002: Socket angelegt -------------------------------------
        try:
            assert sock.exists(), "socket file not present"
            import stat as _stat
            mode = os.stat(sock).st_mode
            assert _stat.S_ISSOCK(mode), "path is not a unix socket"
            e = ev("F002", "socket.txt", f"{sock}\nis_socket=True\n")
            record("F002", "pass", e)
        except AssertionError as e:
            record("F002", "fail", note=str(e))

        # ---- F005: IPC-Roundtrip (ping -> pong) ------------------------
        try:
            r = c.request("ping", {})
            assert r.get("pong") is True, f"unexpected pong: {r}"
            e = ev("F005", "ping.json", json.dumps({"request": "ping", "response": r}, indent=2))
            record("F005", "pass", e, "pong=true")
        except Exception as e:
            record("F005", "fail", note=str(e))

        # ---- F009: Settings get/set-Roundtrip --------------------------
        try:
            before = c.request("config.get", {})
            target = "auto" if before.get("trust_mode") != "auto" else "strict"
            c.request("config.set", {"trust_mode": target})
            after = c.request("config.get", {})
            assert after.get("trust_mode") == target, f"set did not persist: {after.get('trust_mode')}"
            # Persistenz auf Disk?
            settings = home / ".claudestudio/settings.json"
            disk = json.loads(settings.read_text()) if settings.exists() else {}
            e = ev("F009", "config-roundtrip.json", json.dumps(
                {"before": before, "set": target, "after_trust_mode": after.get("trust_mode"),
                 "settings_json_on_disk": disk}, indent=2, ensure_ascii=False))
            record("F009", "pass", e, f"trust_mode {before.get('trust_mode')} -> {target}")
        except Exception as e:
            record("F009", "fail", note=str(e))

        # ---- F013: Library-Discovery (Tasks/Definitions geladen) -------
        try:
            loaded = c.request("library.load_defaults", {})
            tl = c.request("tasks.list", {})
            dl = c.request("definitions.list", {})
            ntasks, ndefs = len(tl.get("tasks", [])), len(dl.get("definitions", []))
            assert ntasks > 0 and ndefs > 0, f"no libraries discovered: {ntasks} tasks, {ndefs} defs"
            sample_tasks = [t.get("name") or t.get("id") for t in tl["tasks"][:5]]
            sample_defs = [d.get("name") for d in dl["definitions"][:5]]
            e = ev("F013", "libraries.json", json.dumps(
                {"load_defaults": loaded, "tasks_count": ntasks, "definitions_count": ndefs,
                 "sample_tasks": sample_tasks, "sample_definitions": sample_defs}, indent=2, ensure_ascii=False))
            record("F013", "pass", e, f"{ntasks} tasks, {ndefs} defs")
        except Exception as e:
            record("F013", "fail", note=str(e))

        # ---- F010: ~/.claudestudio-Layout gebootstrappt ----------------
        try:
            cs = home / ".claudestudio"
            need = ["tasks", "definitions", "models", "sessions.db"]
            present = {n: (cs / n).exists() for n in need}
            assert all(present.values()), f"layout incomplete: {present}"
            listing = subprocess.run(["find", str(cs), "-maxdepth", "2"],
                                     capture_output=True, text=True).stdout
            e = ev("F010", "layout.txt", f"present={present}\n\n{listing}")
            record("F010", "pass", e, "tasks/definitions/models/sessions.db angelegt")
        except AssertionError as e:
            record("F010", "fail", note=str(e))

        # ---- F007: sessions.db angelegt + Schema ----------------------
        try:
            db = home / ".claudestudio/sessions.db"
            assert db.exists(), "sessions.db missing"
            con = sqlite3.connect(str(db))
            tables = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name").fetchall()]
            con.close()
            assert "sessions" in tables and "transcript_fts" in tables, f"core tables missing: {tables}"
            e = ev("F007", "schema.txt", "tables/views:\n  " + "\n  ".join(tables))
            record("F007", "pass", e, f"{len(tables)} tables incl. sessions, transcript_fts")
        except Exception as e:
            record("F007", "fail", note=str(e))

        # ---- F008: FTS5-Volltextsuche auf der echten sessions.db -------
        try:
            db = home / ".claudestudio/sessions.db"
            con = sqlite3.connect(str(db))
            con.execute("INSERT INTO transcript_fts (session_id, source, body) VALUES (?,?,?)",
                        ("probe-f008", "message", "Stripe payment integration for the invoice app"))
            con.commit()
            hit = con.execute(
                "SELECT session_id, source, body FROM transcript_fts WHERE transcript_fts MATCH ?",
                ("payment",)).fetchall()
            miss = con.execute(
                "SELECT count(*) FROM transcript_fts WHERE transcript_fts MATCH ?",
                ("zzznonexistentzzz",)).fetchone()[0]
            con.close()
            assert hit and hit[0][0] == "probe-f008", f"MATCH 'payment' returned {hit}"
            assert miss == 0, f"nonexistent term returned {miss} rows"
            e = ev("F008", "fts5.txt",
                   f"INSERT body='Stripe payment integration for the invoice app'\n"
                   f"MATCH 'payment'  -> {hit}\n"
                   f"MATCH 'zzznonexistentzzz' -> {miss} rows\n")
            record("F008", "pass", e, "MATCH payment -> 1 hit, miss -> 0")
        except Exception as e:
            record("F008", "fail", note=str(e))

        c.close()

    # ---- F006: claude-CLI als Subprozess startbar + Version lesbar -----
    # Es können mehrere claude-Binaries auf dem System liegen (z.B. eine alte,
    # hängende Cask-Version). Wie der cs-claude-Manager probieren wir Kandidaten
    # und nehmen das erste, das innerhalb des Timeouts antwortet.
    try:
        candidates = []
        whichc = subprocess.run(["bash", "-lc", "command -v claude"], capture_output=True, text=True).stdout.strip()
        for cand in [os.path.expanduser("~/.local/bin/claude"), whichc, "/opt/homebrew/bin/claude"]:
            if cand and cand not in candidates and Path(cand).exists():
                candidates.append(cand)
        chosen, ver = None, None
        for cand in candidates:
            try:
                out = subprocess.run([cand, "--version"], capture_output=True, text=True, timeout=12)
                if out.returncode == 0 and (out.stdout or out.stderr).strip():
                    chosen, ver = cand, (out.stdout or out.stderr).strip()
                    break
            except subprocess.TimeoutExpired:
                continue
        assert chosen, f"no responsive claude binary among {candidates}"
        e = ev("F006", "claude-version.txt", f"candidates={candidates}\nchosen={chosen}\nversion={ver}\n")
        record("F006", "pass", e, ver)
    except Exception as e:
        record("F006", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
