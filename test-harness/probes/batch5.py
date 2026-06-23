#!/usr/bin/env python3
"""Verifikation Build-Batch 5: Agentic-OS-Entscheidungslogik gegen den echten Core.

Neu (router.rs): rules.add/list/eval (WENN-DANN-Regeln), routing.route,
queue.order (Prioritäts-Reihenfolge), scheduler.admit (Ressourcen-Limit),
monitor.health_check (echtes HTTP via curl), monitor.cost_guard, supervisor.evaluate
(Restart/Pause/Eskalation). Reine Entscheidungslogik + echtes HTTP. Kein Mock.

Hinweis: Diese Features verifizieren die ENTSCHEIDUNGSLOGIK des Agentic OS. Das
Starten echter Hintergrund-Agenten (Supervisor-Dauerschleife, realer Scan-Start)
braucht die Agent-Runtime und bleibt separat 'blocked'.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b5.log")) as ctx:
        c = P.Client(ctx["sock"])

        # F308: visual WENN-DANN rule engine
        try:
            c.request("rules.add", {"when": {"event": "git.push", "branch": "main"},
                                    "then": ["start_agent:security-scan", "notify:slack"]})
            c.request("rules.add", {"when": {"event": "test.failed"}, "then": ["start_agent:fix-agent"]})
            # event matches branch condition
            fired = c.request("rules.eval", {"event": "git.push", "branch": "main"})["fired"]
            # same event, different branch -> rule must NOT fire
            none = c.request("rules.eval", {"event": "git.push", "branch": "feature/x"})["fired"]
            assert any("security-scan" in str(f["actions"]) for f in fired)
            assert none == []
            record("F308", "pass", ev("F308", "rules.json", {"fired_on_main": fired, "feature_branch": none}),
                   "WENN git.push UND branch==main DANN ... feuert korrekt")
        except Exception as e:
            record("F308", "fail", note=str(e))

        # F304: routing to the matching agent
        try:
            routes = {t: c.request("routing.route", {"task_type": t})["agent"]
                      for t in ["write tests", "security scan", "fix the bug", "update docs"]}
            assert routes["write tests"] == "test-agent"
            assert routes["security scan"] == "security-scan"
            assert routes["fix the bug"] == "fix-agent"
            record("F304", "pass", ev("F304", "routing.json", routes), "Task -> passender Agent geroutet")
        except Exception as e:
            record("F304", "fail", note=str(e))

        # F309: priority queue ordering
        try:
            r = c.request("queue.order", {"tasks": [
                {"id": "a", "priority": "normal"}, {"id": "b", "priority": "critical"},
                {"id": "c", "priority": "background"}, {"id": "d", "priority": "high"}]})
            order = [x for x in r["order"]]
            assert order == ["b", "d", "a", "c"], f"order={order}"
            record("F309", "pass", ev("F309", "queue.json", r), "Critical>High>Normal>Background")
        except Exception as e:
            record("F309", "fail", note=str(e))

        # F310: resource limit enforced
        try:
            ok = c.request("scheduler.admit", {"running": 3, "max_parallel": 4})
            full = c.request("scheduler.admit", {"running": 4, "max_parallel": 4})
            assert ok["admit"] is True and full["admit"] is False
            record("F310", "pass", ev("F310", "admit.json", {"slot_free": ok, "limit_reached": full}),
                   "max-parallel hart erzwungen")
        except Exception as e:
            record("F310", "fail", note=str(e))

        # F313: health monitor pings real endpoints (Qdrant up vs dead port)
        try:
            up = c.request("monitor.health_check", {"url": "http://localhost:6333/healthz"})
            down = c.request("monitor.health_check", {"url": "http://localhost:6399/nope"})
            assert up["status_code"] == 200 and up["alert"] is False
            assert down["alert"] is True
            record("F313", "pass", ev("F313", "health.json", {"up": up, "down": down}),
                   "echtes HTTP: 200 ok, toter Port -> Alert")
        except Exception as e:
            record("F313", "fail", note=str(e))

        # F314: cost guard warn at 80%, stop at 100%
        try:
            ok = c.request("monitor.cost_guard", {"spent": 5.0, "budget": 20.0})
            warn = c.request("monitor.cost_guard", {"spent": 16.5, "budget": 20.0})
            stop = c.request("monitor.cost_guard", {"spent": 21.0, "budget": 20.0})
            assert ok["status"] == "ok" and warn["status"] == "warn" and stop["status"] == "stop"
            record("F314", "pass", ev("F314", "cost.json", {"ok": ok, "warn": warn, "stop": stop}),
                   "Cost-Guard: 80%->warn, 100%->stop")
        except Exception as e:
            record("F314", "fail", note=str(e))

        # F301: supervisor restarts an idle (no-output) agent
        try:
            r = c.request("supervisor.evaluate", {"last_output_ms": 0, "now_ms": 1_000_000, "idle_threshold_ms": 900_000})
            healthy = c.request("supervisor.evaluate", {"last_output_ms": 0, "now_ms": 10_000, "idle_threshold_ms": 900_000})
            assert r["action"] == "restart" and healthy["action"] == "ok"
            record("F301", "pass", ev("F301", "idle.json", {"idle": r, "healthy": healthy}),
                   "kein Output > Schwellwert -> restart (Logik; Dauerschleife=Runtime)")
        except Exception as e:
            record("F301", "fail", note=str(e))

        # F302: supervisor pauses an agent over its token budget
        try:
            r = c.request("supervisor.evaluate", {"tokens_used": 12000, "token_budget": 10000})
            assert r["action"] == "pause"
            record("F302", "pass", ev("F302", "budget.json", r), "Token-Budget überschritten -> pause")
        except Exception as e:
            record("F302", "fail", note=str(e))

        # F303: supervisor escalates an error loop (> 3 repeats)
        try:
            r = c.request("supervisor.evaluate", {"error_repeats": 4})
            ok = c.request("supervisor.evaluate", {"error_repeats": 2})
            assert r["action"] == "escalate" and ok["action"] == "ok"
            record("F303", "pass", ev("F303", "errorloop.json", {"loop": r, "ok": ok}),
                   "gleicher Fehler > 3x -> Eskalation")
        except Exception as e:
            record("F303", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
