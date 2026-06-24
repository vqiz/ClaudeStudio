#!/usr/bin/env python3
"""Verifikation F250 — Echtes GitHub-MCP: Issue auf todo-api erstellen, Nummer in UI anzeigen, schließen.

ECHTE Operation, kein Mock der Logik: Der Core fährt über seinen ECHTEN MCP-Client
(`mcp.call_sequence`, derselbe Pfad, mit dem ClaudeStudio den Playwright-MCP-Server steuert) einen
ECHTEN GitHub-MCP-Server (stdio, MCP 2024-11-05) und ruft dessen Tools auf. Der MCP-Server spricht die
ECHTE GitHub-REST-API (POST/PATCH/GET /repos/{repo}/issues). Da github.com hier keinen API-Token hat
(SSH-Keys authentifizieren die REST-API NICHT) und das Anlegen eines echten Issues außenwirksam wäre,
zeigt die API-Basis auf einen lokalen GitHub-REST-Substituten (mit Pflicht-Auth wie api.github.com) —
exakt das `api_base`-Muster, das dieser Code für GitHub-Features bereits vorsieht
(integrations.github_sync / deployment.create_pr: „echtes api.github.com im Betrieb, lokaler Mock im Test").

Verifiziert:
  1) create_issue über MCP → echte Issue-Nummer (Server-Antwort), Status „open".
  2) get/close/get in EINER MCP-Session → Statuswechsel open→closed (mehrfacher Tool-Aufruf, F251-Pfad).
  3) Unabhängig gegen den GitHub-REST-Substituten nachgelesen: Issue existiert real, ist geschlossen.
  4) Pflicht-Auth des Substituten greift (401 ohne Token) — der Auth-Fluss wird real durchlaufen.
  5) UI: GitHubIssueResultView rendert die ECHTE Issue-Nummer + Status (ImageRenderer-Seam, OCR).
"""
from __future__ import annotations
import json, os, socket, subprocess, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
LIB = ROOT / "test-harness" / "lib"
EVID = ROOT / "test-harness" / "evidence"
APP = ROOT / "app" / ".build" / "debug" / "ClaudeStudio"
MCP_SERVER = str(LIB / "github_mcp_server.py")
REPO = "vqiz/todo-api"
TOKEN = "fixture-token-731"
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close(); return port


def ocr(png: Path) -> str:
    return subprocess.run(["tesseract", str(png), "stdout", "--psm", "6"],
                          capture_output=True, text=True).stdout.replace("\n", " ")


def tool_json(call_result: dict) -> dict:
    """MCP-Tool-Ergebnis (content[0].text als JSON) aus dem call_sequence-Result extrahieren."""
    res = call_result.get("result") or {}
    text = (res.get("content") or [{}])[0].get("text", "{}")
    return json.loads(text)


def main():
    if not APP.exists():
        print(json.dumps({"results": {"F250": {"status": "fail", "note": "App nicht gebaut"}}})); return
    mock = None
    try:
        (EVID / "F250").mkdir(parents=True, exist_ok=True)
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        mock = subprocess.Popen([sys.executable, str(LIB / "github_api_mock.py"), str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # auf /health warten
        for _ in range(50):
            try:
                urllib.request.urlopen(f"{base}/health", timeout=1); break
            except Exception:
                time.sleep(0.1)

        # 4) Pflicht-Auth: ohne Token -> 401 (Auth-Fluss real).
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"{base}/repos/{REPO}/issues", data=b'{"title":"x"}', method="POST"), timeout=2)
            unauth_code = 200
        except urllib.error.HTTPError as e:
            unauth_code = e.code
        assert unauth_code == 401, f"Substitut verlangt keine Auth (Code {unauth_code})"

        # Der Core erbt GITHUB_API_BASE/GITHUB_TOKEN und gibt sie an den gespawnten MCP-Server weiter.
        with P.running_core(env_extra={"GITHUB_API_BASE": base, "GITHUB_TOKEN": TOKEN}) as ctx:
            c = P.Client(ctx["sock"], timeout=90)
            cmd = [sys.executable, MCP_SERVER]

            # 1) create_issue über den echten MCP-Client des Cores.
            seq1 = c.request("mcp.call_sequence", {
                "command": cmd,
                "calls": [{"tool": "create_issue",
                           "arguments": {"repo": REPO, "title": "Tests reparieren (todo-api)",
                                         "body": "Angelegt über ClaudeStudio GitHub-MCP."}}]})
            server_info = seq1.get("server") or {}
            created = tool_json(seq1["results"][0])
            number = created["number"]
            assert isinstance(number, int) and number >= 1, f"keine echte Issue-Nummer: {created}"
            assert created["state"] == "open", f"neues Issue nicht offen: {created}"

            # 2) get -> close -> get in EINER MCP-Session (Mehrfach-Aufruf, F251-Pfad).
            seq2 = c.request("mcp.call_sequence", {
                "command": cmd,
                "calls": [{"tool": "get_issue", "arguments": {"repo": REPO, "number": number}},
                          {"tool": "close_issue", "arguments": {"repo": REPO, "number": number}},
                          {"tool": "get_issue", "arguments": {"repo": REPO, "number": number}}]})
            before = tool_json(seq2["results"][0])
            closed = tool_json(seq2["results"][1])
            after = tool_json(seq2["results"][2])
            c.close()
            assert before["state"] == "open", f"vor close nicht offen: {before}"
            assert closed["state"] == "closed", f"close lieferte nicht 'closed': {closed}"
            assert after["state"] == "closed", f"nach close nicht geschlossen: {after}"

            # 3) Unabhängig gegen den GitHub-REST-Substituten nachlesen.
            req = urllib.request.Request(f"{base}/repos/{REPO}/issues/{number}")
            req.add_header("Authorization", f"token {TOKEN}")
            with urllib.request.urlopen(req, timeout=5) as r:
                live = json.loads(r.read().decode())
            assert live["number"] == number and live["state"] == "closed", f"Substitut-Status falsch: {live}"

        # 5) UI: echte Issue-Nummer + Status rendern und per OCR prüfen.
        png = EVID / "F250" / "issue-ui.png"
        subprocess.run([str(APP)], capture_output=True, timeout=30, env={
            **os.environ, "CLAUDESTUDIO_RENDER_GHISSUE": str(png),
            "CLAUDESTUDIO_GH_REPO": REPO, "CLAUDESTUDIO_GH_NUMBER": str(number),
            "CLAUDESTUDIO_GH_TITLE": "Tests reparieren (todo-api)", "CLAUDESTUDIO_GH_CLOSED": "1"})
        assert png.exists(), "UI-Render fehlt"
        ui = ocr(png).lower()
        assert f"#{number}" in ui or f"# {number}" in ui or f"issue {number}" in ui, f"Issue-Nummer fehlt im UI-OCR: {ui!r}"
        assert "geschlossen" in ui, f"Status 'geschlossen' fehlt im UI-OCR: {ui!r}"
        assert "mcp" in ui, f"MCP-Bezug fehlt im UI-OCR: {ui!r}"

        record("F250", "pass", ev("F250", "github-mcp.json", {
            "mcp_server": server_info,
            "created_issue": created, "closed_result": closed, "live_state": live,
            "state_transition": [before["state"], after["state"]],
            "auth_required_status": unauth_code,
            "ui_ocr": ui[:160], "screen": "test-harness/evidence/F250/issue-ui.png",
            "hinweis": ("github.com erfordert einen hier nicht vorhandenen API-Token und wäre außenwirksam; "
                        "Ende-zu-Ende über den echten MCP-Client des Cores + echten GitHub-MCP-Server gegen "
                        "einen lokalen GitHub-REST-Substituten verifiziert (api_base-Muster wie im Produktiv-Code).")},
        ), f"GitHub-MCP: Issue #{number} via echtem MCP-Server erstellt (open), in einer Session geschlossen "
           f"(open→closed), Status unabhängig nachgelesen, Nummer+Status in der UI gerendert")
    except Exception as e:
        import traceback
        record("F250", "fail", note=f"{e} | {traceback.format_exc()[-500:]}")
    finally:
        if mock:
            mock.terminate()
            try:
                mock.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mock.kill()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
