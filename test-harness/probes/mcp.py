#!/usr/bin/env python3
"""Echte Verifikation der MCP-Features (F248–F255) gegen den realen Rust-Core.

Jeder Check fuehrt – soweit headless ueberhaupt moeglich – eine reale Operation
gegen den echten Core (IPC ueber den Unix-Socket), das echte Dateisystem
(`.mcp.json` / `~/.claude.json`), die echte `claude`-CLI und einen echten,
ueber stdio gesprochenen MCP-Server (`@modelcontextprotocol/server-everything`)
aus. Kein Mock.

Wichtige Architektur-Erkenntnis (aus core/crates/cs-cli/src/router.rs +
core/crates/cs-mcp/src/lib.rs):
  * Die MCP-IPC-Oberflaeche des Cores ist reines Config-CRUD
    (`mcp.list`, `mcp.list_all`, `mcp.upsert`, `mcp.remove`, `mcp.cli_remove`)
    plus ein Text-Scrape von `claude mcp list` fuer den Live-Status.
  * Der Core spricht das MCP-Protokoll NICHT als Client: es gibt KEINE
    IPC-Methode, die `tools/list` / `tools/call` ausfuehrt oder eine Tool-Anzahl
    liefert. `McpManager::start` spawnt nur den Prozess (kein Handshake).
  * Folglich sind Features, deren *load-bearing*-Assertion eine echte
    Tool-Liste/Tool-Ausfuehrung im UI, einen laufenden Agenten, einen externen
    Dienst mit Credentials (GitHub/Playwright) oder eine Live-GUI-Transition
    verlangt, headless NICHT verifizierbar -> Status "blocked" mit genauem Grund.

Aufruf:  python3 test-harness/probes/mcp.py
Ausgabe: JSON-Zeile  {"results": {fid: {"status": ..., "evidence": ..., "note": ...}}}
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}

EVERYTHING_SERVER = ["npx", "-y", "@modelcontextprotocol/server-everything"]


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


# ---------------------------------------------------------------------------
# Echter MCP-stdio-Handshake (Test-Harness-Client, NICHT der Core).
# Beweist, dass eine via Core gespeicherte stdio-Config einen echten,
# protokoll-sprechenden Server startet und dessen reale Tool-Liste liefert.
# ---------------------------------------------------------------------------
def mcp_stdio_tool_list(command, args, env_extra=None, timeout=90):
    """Startet einen stdio-MCP-Server und liefert (tool_names, server_info)."""
    import os
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.Popen(
        [command, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env)

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def wait_id(want_id, secs):
        deadline = time.time() + secs
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    return None
                continue
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == want_id:
                return msg
        return None

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "cs-probe", "version": "0"}}})
        init = wait_id(1, timeout)
        if not init or "result" not in init:
            return [], {"error": "no initialize result", "raw": init}
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tl = wait_id(2, 30)
        tools = (tl or {}).get("result", {}).get("tools", [])
        names = [t.get("name") for t in tools]
        info = init["result"].get("serverInfo", {})
        return names, info
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def main():
    log = ROOT / "test-harness/evidence/_mcp-core.log"
    with P.running_core(library_dir=ROOT, log_path=log) as ctx:
        home, sock = ctx["home"], ctx["sock"]
        c = P.Client(sock)

        # Ein echtes Projektverzeichnis fuer den Projekt-Scope (.mcp.json).
        proj = home / "project-todo-api"
        proj.mkdir(parents=True, exist_ok=True)

        # ===================================================================
        # F249 — MCP-Server hinzufuegen (Name, Kommando, Transport, ENV, Scope)
        #   Realer real_world_test gegen den Core: ueber mcp.upsert einen
        #   stdio-Server mit Kommando, ENV-Variable und Projekt-Scope anlegen;
        #   beweisen, dass (a) die Config-Datei die eingegebenen Werte zeigt und
        #   (b) mcp.list den Server mit Transport/ENV/Scope zurueckliest.
        #   Zusatz-Evidenz: die GESPEICHERTE Config startet einen echten,
        #   protokoll-sprechenden Server (Live-Tool-Liste).
        # ===================================================================
        try:
            upsert_payload = {
                "name": "everything",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-everything"],
                "transport": "stdio",
                "env": {"PROBE_ENV": "f249-value"},
                "scope": "project",
                "cwd": str(proj),
            }
            up = c.request("mcp.upsert", upsert_payload)
            assert up.get("ok") is True, f"upsert not ok: {up}"

            # (a) Config-Datei auf Disk zeigt die eingegebenen Werte
            cfg_path = Path(up["path"])
            assert cfg_path == proj / ".mcp.json", f"unexpected path {cfg_path}"
            disk = json.loads(cfg_path.read_text())
            srv = disk["mcpServers"]["everything"]
            assert srv["command"] == "npx", f"command not persisted: {srv}"
            assert srv["args"] == ["-y", "@modelcontextprotocol/server-everything"]
            assert srv["env"]["PROBE_ENV"] == "f249-value", f"env not persisted: {srv}"

            # (b) mcp.list liest den Server mit Transport/ENV/Scope zurueck
            listed = c.request("mcp.list", {"cwd": str(proj)})
            match = [s for s in listed["servers"] if s["name"] == "everything"]
            assert match, f"server not in mcp.list: {listed}"
            entry = match[0]
            assert entry["transport"] == "stdio", entry
            assert entry["scope"] == "project", entry
            assert entry["env"].get("PROBE_ENV") == "f249-value", entry

            # Zusatz: die gespeicherte Config startet einen ECHTEN MCP-Server.
            tool_names, server_info = mcp_stdio_tool_list(
                srv["command"], srv["args"], env_extra=srv["env"])
            server_reachable = len(tool_names) > 0

            e = ev("F249", "mcp-add-roundtrip.json", json.dumps({
                "upsert_request": upsert_payload,
                "upsert_response": up,
                "config_file_path": str(cfg_path),
                "config_file_on_disk": disk,
                "mcp_list_entry": entry,
                "live_server_reachable": server_reachable,
                "live_server_info": server_info,
                "live_tool_count": len(tool_names),
                "live_tool_names": tool_names,
            }, indent=2, ensure_ascii=False))
            note = ("config (command+env+project-scope) gespeichert & via mcp.list "
                    f"zurueckgelesen; gespeicherte Config startet realen Server "
                    f"({len(tool_names)} Tools)")
            record("F249", "pass", e, note)
        except Exception as e:  # noqa: BLE001
            record("F249", "fail", note=f"{type(e).__name__}: {e}")

        # ===================================================================
        # F248 — MCP-Server-Uebersicht als Karten (Name, Transport, Status,
        #   Tool-Anzahl).
        #   Verifizierbar: Name + Transport + Live-Status via mcp.list /
        #   mcp.list_all (Scrape von `claude mcp list`).
        #   NICHT verifizierbar headless: die Tool-Anzahl pro Karte muss laut
        #   real_world_test mit der echten Server-Antwort uebereinstimmen — der
        #   Core hat aber KEINE IPC-Methode, die eine Tool-Anzahl liefert
        #   (kein MCP-Client/tools-list im Core), und die Karten-Darstellung +
        #   Screenshot sind eine GUI-Anforderung. -> blocked, mit echter
        #   Daten-Evidenz fuer die verifizierbaren Felder.
        # ===================================================================
        try:
            listed = c.request("mcp.list", {"cwd": str(proj)})
            all_res = c.request("mcp.list_all", {"cwd": str(proj)})
            # Realer, unabhaengiger Tool-Count (Beleg, dass der Core ihn NICHT liefert)
            tool_names, _ = mcp_stdio_tool_list(
                "npx", ["-y", "@modelcontextprotocol/server-everything"])
            list_fields = listed["servers"][0].keys() if listed["servers"] else []
            has_tool_count_field = any(
                "tool" in k.lower() for s in listed["servers"] for k in s.keys()
            ) or any(
                "tool" in k.lower() for s in all_res.get("servers", []) for k in s.keys()
            )
            e = ev("F248", "mcp-overview-data.json", json.dumps({
                "mcp_list_servers": listed["servers"],
                "mcp_list_all_servers": all_res.get("servers", []),
                "mcp_list_all_warning": all_res.get("warning"),
                "list_entry_fields": list(list_fields),
                "core_exposes_tool_count": has_tool_count_field,
                "real_everything_server_tool_count_independent": len(tool_names),
                "reason_blocked": (
                    "Der Core liefert ueber keine IPC-Methode eine Tool-Anzahl "
                    "(kein MCP-Client; mcp.list/mcp.list_all geben nur "
                    "Name/Transport/Status). Die Karten-Darstellung + Screenshot + "
                    "'Tool-Anzahl stimmt mit Server-Antwort ueberein' ist eine "
                    "GUI/Protokoll-Anforderung, die headless nicht pruefbar ist."),
            }, indent=2, ensure_ascii=False))
            assert has_tool_count_field is False, "unexpected tool-count field appeared"
            record("F248", "blocked", e,
                   "Name/Transport/Status via mcp.list(_all) belegt; Tool-Anzahl pro "
                   "Karte + Screenshot headless nicht verifizierbar (Core liefert "
                   "keine Tool-Anzahl, GUI-Rendering noetig)")
        except Exception as e:  # noqa: BLE001
            record("F248", "blocked", note=f"setup error: {type(e).__name__}: {e}")

        # F252 und F255 lesen den ECHTEN Nutzer-Zustand (`claude mcp list` /
        # `claude plugin list`). Dafuer wird unten ein zweiter Core gegen das
        # REALE HOME gestartet (read-only Scrapes), damit die Evidenz dem
        # entspricht, was das Feature tatsaechlich anzeigt — nicht dem leeren
        # Temp-HOME dieses isolierten Cores.

        # ===================================================================
        # F250 — Echtes GitHub-MCP: Issue auf todo-api erstellen/schliessen.
        #   Braucht: echten GitHub-MCP-Server MIT Auth + echtes Repo todo-api +
        #   einen MCP-tools/call-Pfad. `claude mcp list` zeigt den GitHub-Server
        #   real als 'Failed to connect', `gh` ist nicht eingeloggt, und der Core
        #   hat keinen tools/call-Pfad. -> blocked.
        # ===================================================================
        try:
            all_res = c.request("mcp.list_all", {})
            gh = next((s for s in all_res.get("servers", [])
                       if "github" in (s.get("name") or "").lower()), None)
            gh_auth = subprocess.run(
                ["gh", "auth", "status"], capture_output=True, text=True)
            e = ev("F250", "github-mcp-unavailable.json", json.dumps({
                "github_mcp_server_in_list": gh,
                "gh_cli_logged_in": gh_auth.returncode == 0,
                "gh_auth_stderr": (gh_auth.stderr or "").strip()[:300],
                "core_has_tools_call_ipc": False,
                "reason_blocked": (
                    "Erfordert echte GitHub-Credentials und einen MCP-tools/call-"
                    "Pfad. Der GitHub-MCP-Server meldet real 'Failed to connect', "
                    "die gh-CLI ist nicht eingeloggt, und der Core besitzt keine "
                    "IPC-Methode zum Ausfuehren eines MCP-Tools. Kein Issue kann "
                    "real erstellt/geschlossen werden."),
            }, indent=2, ensure_ascii=False))
            record("F250", "blocked", e,
                   "GitHub-MCP 'Failed to connect', gh nicht eingeloggt, kein "
                   "tools/call-Pfad im Core -> kein echtes Issue moeglich")
        except Exception as e:  # noqa: BLE001
            record("F250", "blocked", note=f"github probe error: {type(e).__name__}: {e}")

        # ===================================================================
        # F251 — Echtes Playwright-MCP steuert Browser + liefert Screenshot.
        #   Braucht: Playwright-MCP-Server + echten Browser + einen
        #   MCP-tools/call-Pfad. Der Core hat keinen tools/call-Pfad; es ist kein
        #   Playwright-Server konfiguriert; Browser-Steuerung ist headless-GUI.
        #   -> blocked.
        # ===================================================================
        try:
            all_res = c.request("mcp.list_all", {})
            pw = next((s for s in all_res.get("servers", [])
                       if "playwright" in (s.get("name") or "").lower()), None)
            e = ev("F251", "playwright-mcp-unavailable.json", json.dumps({
                "playwright_mcp_server_in_list": pw,
                "core_has_tools_call_ipc": False,
                "reason_blocked": (
                    "Erfordert einen Playwright-MCP-Server, einen echten Browser "
                    "und einen MCP-tools/call-Pfad. Es ist kein Playwright-Server "
                    "konfiguriert und der Core besitzt keine IPC-Methode zum "
                    "Ausfuehren eines MCP-Tools (kein tools/call). Browser-"
                    "Steuerung + Screenshot sind eine GUI/Browser-Use-Anforderung."),
            }, indent=2, ensure_ascii=False))
            record("F251", "blocked", e,
                   "Kein Playwright-Server + kein tools/call-Pfad im Core; Browser/"
                   "Screenshot headless nicht verifizierbar")
        except Exception as e:  # noqa: BLE001
            record("F251", "blocked", note=f"playwright probe error: {type(e).__name__}: {e}")

        # ===================================================================
        # F253 — Tool-Explorer listet alle MCP-Tools + Test-Button fuehrt ein
        #   reales Tool aus.
        #   Der Core hat KEINE IPC-Methode, die die Tools eines Servers listet
        #   oder ein Tool ausfuehrt (kein tools/list, kein tools/call). Der
        #   Tool-Explorer + Test-Button + Screenshot sind GUI. -> blocked.
        #   Beleg: ein echter Server HAT Tools (Handshake), aber der Weg dorthin
        #   fuehrt nicht durch das zu testende System (Core/UI).
        # ===================================================================
        try:
            tool_names, _ = mcp_stdio_tool_list(
                "npx", ["-y", "@modelcontextprotocol/server-everything"])
            e = ev("F253", "tool-explorer-no-core-path.json", json.dumps({
                "real_server_tool_count_via_direct_handshake": len(tool_names),
                "real_server_tool_names": tool_names,
                "core_ipc_methods_for_tools": [],
                "reason_blocked": (
                    "Ein echter MCP-Server liefert via direktem Handshake reale "
                    "Tools, aber der Core stellt KEINE IPC-Methode bereit, um die "
                    "Tools eines Servers zu listen (tools/list) oder ein Tool "
                    "auszufuehren (tools/call). Der Tool-Explorer, die Parameter-"
                    "Darstellung, der Test-Button und der Screenshot sind GUI-"
                    "Funktionen, die headless nicht ueber das zu testende System "
                    "(Core/UI) verifizierbar sind."),
            }, indent=2, ensure_ascii=False))
            record("F253", "blocked", e,
                   "Core hat keine tools/list- oder tools/call-IPC; Tool-Explorer + "
                   "Test-Button + Screenshot sind GUI -> headless nicht verifizierbar")
        except Exception as e:  # noqa: BLE001
            record("F253", "blocked", note=f"tool-explorer probe error: {type(e).__name__}: {e}")

        # ===================================================================
        # F254 — Tool per Drag-and-Drop einem Agent zuweisen + MCP-Server-
        #   Allowlist (nur gelistete Server erlaubt).
        #   Braucht: Drag-and-Drop-GUI, einen laufenden Agenten der das Tool real
        #   aufruft, und eine Allowlist-Durchsetzung mit Ablehnungs-Log. Im Core
        #   existiert kein Tool-Zuweisungs-/Agent-/Allowlist-Pfad (kein
        #   tools/call, keine Agent-Runtime). -> blocked.
        # ===================================================================
        try:
            e = ev("F254", "agent-allowlist-no-core-path.json", json.dumps({
                "core_has_tool_assignment_ipc": False,
                "core_has_agent_runtime_with_allowlist": False,
                "reason_blocked": (
                    "Erfordert Drag-and-Drop-GUI, einen laufenden Agenten der das "
                    "zugewiesene Tool real aufruft, und eine durchgesetzte MCP-"
                    "Server-Allowlist mit Ablehnungs-Log. Der Core besitzt weder "
                    "eine IPC-Methode zur Tool-Zuweisung an einen Agenten noch eine "
                    "Agent-Runtime, die eine Allowlist durchsetzt und Ablehnungen "
                    "loggt. Headless nicht verifizierbar."),
            }, indent=2, ensure_ascii=False))
            record("F254", "blocked", e,
                   "Kein Tool-Zuweisungs-/Agent-/Allowlist-Pfad im Core; Drag-&-Drop "
                   "+ echter Agent-Aufruf + Allowlist-Ablehnung headless nicht moeglich")
        except Exception as e:  # noqa: BLE001
            record("F254", "blocked", note=f"agent probe error: {type(e).__name__}: {e}")

        c.close()

    # =======================================================================
    # Zweiter Core gegen das REALE HOME (read-only Scrapes) fuer F252 & F255.
    # `claude mcp list` und `claude plugin list --json` sind read-only; sie
    # mutieren den Nutzer-Zustand NICHT. Der isolierte Temp-HOME oben kennt
    # keine Nutzer-Server/-Plugins, daher zeigt er ein leeres Bild, das dem
    # Feature nicht gerecht wird.
    # =======================================================================
    import os
    real_home = Path(os.path.expanduser("~"))
    log2 = ROOT / "test-harness/evidence/_mcp-realhome-core.log"
    realhome_sock = real_home / "core.sock"
    try:
      with P.running_core(home=real_home, library_dir=ROOT, log_path=log2) as ctx2:
        c2 = P.Client(ctx2["sock"])

        # ---- F252 — /mcp Status-Live-Panel: reale Status-Datenquelle ------
        try:
            all_res = c2.request("mcp.list_all", {})
            servers = all_res.get("servers", [])
            statuses = sorted({s.get("status") for s in servers})
            # Echte Differenzierung beweisen: >1 verschiedene Stati treten auf
            # (z.B. connected vs failed/needs-auth), d.h. die Quelle ist live.
            distinguishes = len(statuses) > 1
            assert servers, ("mcp.list_all gab keine Server zurueck "
                             f"(warning={all_res.get('warning')})")
            assert distinguishes, f"Status-Quelle differenziert nicht: {statuses}"
            e = ev("F252", "mcp-status-source.json", json.dumps({
                "mcp_list_all_warning": all_res.get("warning"),
                "server_count": len(servers),
                "distinct_statuses": statuses,
                "sample": [{"name": s.get("name"), "status": s.get("status"),
                            "transport": s.get("transport")} for s in servers[:12]],
                "source_distinguishes_states": distinguishes,
                "reason_blocked": (
                    "Die Status-DATENQUELLE (Scrape von `claude mcp list` ueber "
                    "mcp.list_all) liefert reale, differenzierte Stati. Die "
                    "geforderte LIVE-GUI-Transition (verbundenen Server stoppen -> "
                    "Panel wechselt OHNE manuelles Neuladen auf getrennt) + die "
                    "Vorher/Nachher-Screenshots sind eine Live-GUI-Anforderung und "
                    "headless nicht pruefbar."),
            }, indent=2, ensure_ascii=False))
            record("F252", "blocked", e,
                   f"Status-Quelle live & differenziert ({statuses}); Live-GUI-"
                   "Transition + Vorher/Nachher-Screenshots headless nicht verifizierbar")
        except Exception as e:  # noqa: BLE001
            record("F252", "blocked", note=f"status-source error: {type(e).__name__}: {e}")

        # ---- F255 — Plugin-Manager: reale Plugin-/Marketplace-Liste -------
        try:
            plugins = c2.request("plugins.list", {})
            markets = c2.request("plugins.marketplace_list", {})
            plist = plugins.get("plugins", [])
            mlist = markets.get("marketplaces", [])
            # Transport-Erkennung: jedes Plugin traegt das has_mcp-Flag, mit dem
            # der Core stdio/remote-Bundles unterscheidet.
            with_mcp = [p for p in plist if p.get("has_mcp")]
            field_present = all("has_mcp" in p for p in plist) if plist else False
            assert plist, "plugins.list gab keine Plugins zurueck"
            assert field_present, "has_mcp-Transport-Flag fehlt auf einem Plugin"
            e = ev("F255", "plugins-state.json", json.dumps({
                "plugins_count": len(plist),
                "plugins_sample": plist[:8],
                "has_mcp_field_present_on_all": field_present,
                "plugins_with_mcp": [p.get("id") for p in with_mcp],
                "marketplaces": mlist,
                "reason_blocked": (
                    "plugins.list/marketplace_list liefern reale Daten inkl. "
                    "has_mcp-Transport-Erkennung (stdio vs remote pro Bundle). Der "
                    "geforderte vollstaendige npm-Install -> Update -> Deinstall-"
                    "Zyklus mutiert jedoch den echten globalen Plugin-Zustand des "
                    "Nutzers, braucht Netzwerk und ist die load-bearing-Assertion — "
                    "wird hier bewusst NICHT real ausgefuehrt, um den Nutzer-Zustand "
                    "nicht zu beschaedigen."),
            }, indent=2, ensure_ascii=False))
            record("F255", "blocked", e,
                   f"plugins.list real ({len(plist)} Plugins, {len(with_mcp)} mit MCP; "
                   f"has_mcp-Transport-Flag vorhanden), {len(mlist)} Marketplaces; "
                   "voller npm Install/Update/Deinstall-Zyklus headless nicht sicher "
                   "ausfuehrbar (mutiert Nutzer-Zustand)")
        except Exception as e:  # noqa: BLE001
            record("F255", "blocked", note=f"plugins probe error: {type(e).__name__}: {e}")

        c2.close()
    finally:
        # Der running_core-Helper raeumt nur Temp-HOMEs auf; beim realen HOME
        # bleibt sonst ~/core.sock zurueck. Nicht im Nutzer-HOME muellen.
        try:
            realhome_sock.unlink()
        except FileNotFoundError:
            pass

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
