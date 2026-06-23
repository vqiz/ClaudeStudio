#!/usr/bin/env python3
"""Verifikation Build-Batch 9: Agent Studio (Config-CRUD/Checks) + Model-Router.

Neu (router.rs): agents.create/list/get/update/delete, agents.check_tool (Enforcement),
agents.render_prompt ({{var}}), agents.context (Definition-Injektion); model_router.route/
set/resolve/fallback/cost_compare. File/Logik-basiert gegen den echten Core. Kein Mock.
"""
from __future__ import annotations
import json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}
HOME = Path(tempfile.mkdtemp(prefix="cs-b9-home-"))


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def core():
    return P.running_core(home=HOME, library_dir=ROOT, log_path=Path("/tmp/b9.log"))


def main():
    agent_id = None
    with core() as ctx:
        c = P.Client(ctx["sock"])
        c.request("library.load_defaults", {})

        # F106/F107/F114: create agent with full designer config
        try:
            r = c.request("agents.create", {
                "name": "Security Scanner", "icon": "shield", "color": "#EA4335",
                "description": "OWASP scan", "model": "sonnet",
                "allowed_tools": ["Bash", "Read", "Grep"],
                "system_prompt": "You are {{role}} for project {{project}}.",
                "definitions": ["Error Handling Standard"],
                "trigger": "event", "usd_budget": 2.0, "token_budget": 50000,
                "timeout_s": 600, "retry": 2, "priority": "high",
            })
            agent_id = r["id"]
            g = c.request("agents.get", {"id": agent_id})["agent"]
            assert g["name"] == "Security Scanner" and g["model"] == "sonnet"
            record("F106", "pass", ev("F106", "identity.json", g), "Agent-Identität angelegt+persistiert")
            assert g["allowed_tools"] == ["Bash", "Read", "Grep"]
            record("F107", "pass", ev("F107", "tools.json", g["allowed_tools"]), "Allowed-Tools-Matrix gespeichert")
            assert g["usd_budget"] == 2.0 and g["timeout_s"] == 600 and g["retry"] == 2 and g["priority"] == "high"
            record("F114", "pass", ev("F114", "effort.json",
                   {k: g[k] for k in ["usd_budget", "timeout_s", "retry", "priority"]}),
                   "Effort-Limits (USD/Timeout/Retry/Priorität) konfiguriert+persistiert")
        except Exception as e:
            for fid in ["F106", "F107", "F114"]:
                if fid not in results: record(fid, "fail", note=str(e))

        # F108: tool enforcement
        try:
            ok = c.request("agents.check_tool", {"id": agent_id, "tool": "Bash"})
            no = c.request("agents.check_tool", {"id": agent_id, "tool": "WebSearch"})
            assert ok["allowed"] is True and no["allowed"] is False
            record("F108", "pass", ev("F108", "enforcement.json", {"bash": ok, "websearch": no}),
                   "abgewähltes Tool (WebSearch) wird abgewiesen")
        except Exception as e:
            record("F108", "fail", note=str(e))

        # F109: system prompt {{variable}} substitution
        try:
            r = c.request("agents.render_prompt", {"id": agent_id, "vars": {"role": "auditor", "project": "todo-api"}})
            assert r["prompt"] == "You are auditor for project todo-api."
            record("F109", "pass", ev("F109", "render.json", r), "{{variable}} beim Run ersetzt")
        except Exception as e:
            record("F109", "fail", note=str(e))

        # F115: assigned definitions auto-injected into the agent's context
        try:
            r = c.request("agents.context", {"id": agent_id})
            assert "Error Handling Standard" in r["active_definitions"] and r["tokens"] > 0
            record("F115", "pass", ev("F115", "agent-context.json",
                   {"definitions": r["definitions"], "excerpt": r["active_definitions"][:160]}),
                   "zugeordnete Definition automatisch injiziert")
        except Exception as e:
            record("F115", "fail", note=str(e))

        # --- model router ---
        try:
            routes = {t: c.request("model_router.route", {"task_type": t})["model"]
                      for t in ["update docs", "feature implementation", "architecture design", "monitor"]}
            assert routes["update docs"] == "haiku"
            assert routes["feature implementation"] == "sonnet"
            assert routes["architecture design"] == "opus"
            record("F131", "pass", ev("F131", "route.json", routes), "Routing nach Task-Typ Haiku/Sonnet/Opus")
        except Exception as e:
            record("F131", "fail", note=str(e))

        try:
            c.request("model_router.set", {"task_type": "feature implementation", "model": "opus"})
            r = c.request("model_router.route", {"task_type": "feature implementation"})
            assert r["model"] == "opus" and r["source"] == "configured"
            record("F132", "pass", ev("F132", "threshold.json", r), "Routing-Schwellwert konfigurierbar")
        except Exception as e:
            record("F132", "fail", note=str(e))

        try:
            r = c.request("model_router.resolve", {"task_type": "update docs", "agent_override": "opus"})
            assert r["model"] == "opus" and r["source"] == "agent_override"
            record("F133", "pass", ev("F133", "override.json", r), "Agent-Override schlägt Router")
        except Exception as e:
            record("F133", "fail", note=str(e))

        try:
            o = c.request("model_router.fallback", {"model": "opus"})
            s = c.request("model_router.fallback", {"model": "sonnet"})
            h = c.request("model_router.fallback", {"model": "haiku"})
            assert o["fallback"] == "sonnet" and s["fallback"] == "haiku" and h["exhausted"] is True
            record("F134", "pass", ev("F134", "fallback.json", {"opus": o, "sonnet": s, "haiku": h}),
                   "Fallback-Chain Opus->Sonnet->Haiku")
        except Exception as e:
            record("F134", "fail", note=str(e))

        try:
            r = c.request("model_router.cost_compare", {"input_tokens": 100000, "output_tokens": 20000, "routed_model": "haiku"})
            assert r["saved"] is True and r["savings_usd"] > 0 and r["routed_cost_usd"] < r["opus_cost_usd"]
            record("F135", "pass", ev("F135", "cost-compare.json", r), "Kostenvergleich bestätigt Einsparung")
        except Exception as e:
            record("F135", "fail", note=str(e))

        c.close()

    # F119: config persists + reloads after a core restart (same HOME)
    try:
        with core() as ctx:
            c = P.Client(ctx["sock"])
            g = c.request("agents.get", {"id": agent_id})["agent"]
            assert g["name"] == "Security Scanner" and g["allowed_tools"] == ["Bash", "Read", "Grep"]
            assert g["token_budget"] == 50000
            record("F119", "pass", ev("F119", "reload.json", g), "Agent-Config nach Core-Neustart korrekt geladen")
            c.close()
    except Exception as e:
        record("F119", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
