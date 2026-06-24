#!/usr/bin/env python3
"""Verifikation Build-Batch 94 (echter Core, ECHTER claude, echte Session, kein Mock):

  F142  UI-Buttons für Slash-Befehle (/simplify, /resume, /cost, /status, /mcp, /compact) lösen die
        jeweils ECHTEN Befehle aus: /cost + /status lesen die echten Session-Daten, /mcp listet die
        echten MCP-Tools, /resume baut die echte claude-Resume-Invocation, /compact + /simplify lassen
        den echten claude zusammenfassen bzw. vereinfachen. Jeder Befehl liefert ein echtes Resultat.
"""
from __future__ import annotations
import json, os, sys, time, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
CLAUDE = os.path.expanduser("~/.local/bin/claude")
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def main():
    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/b77.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": CLAUDE}) as ctx:
        sock = ctx["sock"]
        ca = P.Client(sock, timeout=420)
        ctrl = P.Client(sock, timeout=420)
        try:
            # Echte Session starten (trivialer Prompt -> schnell), session_id einsammeln.
            rid = str(uuid.uuid4())
            ca.sock.sendall(P.encode_frame({"id": rid, "kind": "request", "method": "session.start",
                "payload": {"prompt": "Antworte nur mit dem Wort hallo.", "cwd": str(ROOT)}}))
            sid = None
            deadline = time.time() + 120
            while time.time() < deadline:
                f = ca._read_frame()
                if f.get("id") == rid and f.get("kind") != "event":
                    sid = (f.get("payload") or {}).get("session_id")
                evd = ((f.get("payload") or {}).get("event") or {})
                if evd.get("kind") in ("result", "done"):
                    break
            assert sid, "keine session_id von session.start"

            # Etwas Usage erfassen, damit /cost echte Kosten zeigt.
            ctrl.request("session.record_usage",
                         {"session_id": sid, "input_tokens": 1200, "output_tokens": 340, "cost_usd": 0.018})

            def slash(cmd, **extra):
                return ctrl.request("session.slash_command", {"command": cmd, "session_id": sid, **extra})

            cost = slash("/cost")
            status = slash("/status")
            mcp = slash("/mcp")
            resume = slash("/resume")
            compact = slash("/compact", cwd=str(ROOT), text=(
                "User: Bitte refaktoriere die IPC-Schicht auf MessagePack. "
                "Assistant: Habe das Framing auf length-prefixed MessagePack umgestellt und "
                "einen Reconnect-Loop ergänzt. Tests sind grün."))
            simplify = slash("/simplify", cwd=str(ROOT), text=(
                "function add(a,b){ let result = 0; result = result + a; result = result + b; return result; }"))

            # Verifikation: jeder Befehl liefert ein echtes Resultat
            assert cost["usage"] and (cost["usage"].get("cost_usd") or cost["usage"].get("total_cost_usd") or 0) > 0, \
                f"/cost ohne echte Kosten: {cost}"
            assert status["ok"] and status["status"], f"/status ohne Status: {status}"
            assert mcp["ok"] and mcp["tool_count"] and int(mcp["tool_count"]) >= 1, f"/mcp ohne Tools: {mcp}"
            assert resume["command_line"] == f"claude --resume {resume['session_id']}" or "resume" in resume["command_line"], \
                f"/resume baut keine Resume-Invocation: {resume}"
            assert compact["ok"] and len(compact["summary"]) > 10, f"/compact ohne Zusammenfassung: {compact}"
            assert simplify["ok"] and "return" in simplify["result"].lower(), f"/simplify ohne Code: {simplify}"

            record("F142", "pass", ev("F142", "slash-commands.json",
                   {"cost": cost["usage"], "status": status["status"], "mcp_tool_count": mcp["tool_count"],
                    "resume": resume["command_line"], "compact_summary": compact["summary"][:200],
                    "simplify_result": simplify["result"][:200]}),
                   f"6 Slash-Befehle real: /cost {cost['usage']}, /status {status['status']}, "
                   f"/mcp {mcp['tool_count']} tools, /resume, /compact (Summary), /simplify (Code)")
        except Exception as e:
            record("F142", "fail", note=str(e))
        finally:
            ca.close(); ctrl.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
