#!/usr/bin/env python3
"""Verifikation Build-Batch 6: Token-/Kosten-Tracking gegen den echten Core.

Neu: usage-Tabelle in cs-sessions + session.record_usage, session.get (usage-Totals),
session.stats (Gesamtkosten/cache-hit/teuerste Session), cost.summary (Dashboard nach
model/agent/project), cost.cache_hit_rate. Echte SQLite-Persistenz, echter Core.
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
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b6.log")) as ctx:
        c = P.Client(ctx["sock"])
        sa = c.request("session.create", {"title": "Arch", "cwd": str(ROOT)})["id"]
        sb = c.request("session.create", {"title": "Docs", "cwd": str(ROOT)})["id"]

        c.request("session.record_usage", {"session_id": sa, "model": "opus", "agent": "arch",
            "project": "todo-api", "input_tokens": 1000, "output_tokens": 500,
            "cache_read_tokens": 2000, "cache_creation_tokens": 100, "cost_usd": 0.50})
        c.request("session.record_usage", {"session_id": sb, "model": "haiku", "agent": "docs",
            "project": "landing", "input_tokens": 4000, "output_tokens": 200,
            "cache_read_tokens": 1000, "cache_creation_tokens": 0, "cost_usd": 0.05})

        # F153: token breakdown persisted + readable per session
        try:
            u = c.request("session.get", {"id": sa})["usage"]
            assert u["input_tokens"] == 1000 and u["output_tokens"] == 500
            assert u["cache_read_tokens"] == 2000 and u["cache_creation_tokens"] == 100
            record("F153", "pass", ev("F153", "token-breakdown.json", u),
                   "Input/Output/Cache-Token persistiert + per Session lesbar")
        except Exception as e:
            record("F153", "fail", note=str(e))

        # F154: USD cost persisted
        try:
            u = c.request("session.get", {"id": sa})["usage"]
            assert abs(u["cost_usd"] - 0.50) < 1e-9
            record("F154", "pass", ev("F154", "cost.json", u), "USD-Kosten persistiert")
        except Exception as e:
            record("F154", "fail", note=str(e))

        # F164: most expensive session in stats
        try:
            st = c.request("session.stats", {})
            me = st["most_expensive_session"]
            assert me["session_id"] == sa and abs(me["cost_usd"] - 0.50) < 1e-9
            assert abs(st["total_cost_usd"] - 0.55) < 1e-9
            record("F164", "pass", ev("F164", "most-expensive.json", st),
                   "teuerste Session + Gesamtkosten korrekt")
        except Exception as e:
            record("F164", "fail", note=str(e))

        # F277: cost dashboard grouped by model / agent / project
        try:
            by_model = {g["key"]: g["cost_usd"] for g in c.request("cost.summary", {"group_by": "model"})["groups"]}
            by_agent = {g["key"]: g["cost_usd"] for g in c.request("cost.summary", {"group_by": "agent"})["groups"]}
            by_proj = {g["key"]: g["cost_usd"] for g in c.request("cost.summary", {"group_by": "project"})["groups"]}
            assert by_model["opus"] == 0.50 and by_model["haiku"] == 0.05
            assert by_agent["arch"] == 0.50 and by_proj["todo-api"] == 0.50
            record("F277", "pass", ev("F277", "dashboard.json",
                   {"by_model": by_model, "by_agent": by_agent, "by_project": by_proj}),
                   "Kosten-Dashboard nach Modell/Agent/Projekt")
        except Exception as e:
            record("F277", "fail", note=str(e))

        # F278: model breakdown carries input/output/cache token fields
        try:
            groups = c.request("cost.summary", {"group_by": "model"})["groups"]
            opus = next(g for g in groups if g["key"] == "opus")
            assert opus["input_tokens"] == 1000 and opus["output_tokens"] == 500
            assert opus["cache_read_tokens"] == 2000 and opus["cache_creation_tokens"] == 100
            record("F278", "pass", ev("F278", "model-breakdown.json", opus),
                   "Model-Breakdown Input/Output/Cache-Read/Cache-Creation")
        except Exception as e:
            record("F278", "fail", note=str(e))

        # F281: cache hit rate = cache_read / (cache_read + input)
        try:
            rate = c.request("cost.cache_hit_rate", {})["cache_hit_rate"]
            # (2000+1000) / ((2000+1000)+(1000+4000)) = 3000/8000 = 0.375
            assert abs(rate - 0.375) < 1e-9, f"rate={rate}"
            record("F281", "pass", ev("F281", "cache-rate.json", {"cache_hit_rate": rate, "expected": 0.375}),
                   "Cache-Hit-Rate korrekt berechnet")
        except Exception as e:
            record("F281", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
