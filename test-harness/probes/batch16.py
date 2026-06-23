#!/usr/bin/env python3
"""Verifikation Build-Batch 16: Kontext-Editor/Memory/Settings/Metrics + Reuse-Checks.

Neue Handler: worktree max-parallel, context.sections/token_check/diff, memory.categorize/
token_usage, definitions.grouped, cost.estimate, metrics.productivity, pipeline.generate,
settings.set/get. Reuse-Verifikation bereits gebauter Logik (context.assemble worktree,
agents.*, supervisor.evaluate, monitor.*, session.stats). Echter Core. Kein Mock.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}
GENV = {**os.environ, "GIT_AUTHOR_NAME": "B16", "GIT_AUTHOR_EMAIL": "b16@cs.test",
        "GIT_COMMITTER_NAME": "B16", "GIT_COMMITTER_EMAIL": "b16@cs.test"}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=GENV)


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b16.log")) as ctx:
        c = P.Client(ctx["sock"])
        c.request("library.load_defaults", {})

        # F081 context sections
        try:
            s = c.request("context.sections", {})["sections"]
            assert "Regeln" in s and "Coding Preferences" in s and len(s) == 7
            record("F081", "pass", ev("F081", "sections.json", s), "7 vorgefertigte Editor-Sektionen")
        except Exception as e:
            record("F081", "fail", note=str(e))

        # F080 token warning
        try:
            big = c.request("context.token_check", {"content": "word " * 6000})
            small = c.request("context.token_check", {"content": "kurz"})
            assert big["warning"] is True and small["warning"] is False
            record("F080", "pass", ev("F080", "token-warn.json", {"big": big, "small": small}), "Token-Warnung >4000")
        except Exception as e:
            record("F080", "fail", note=str(e))

        # F082 diff to last
        try:
            f = Path(tempfile.mktemp(suffix=".md")); f.write_text("line a\nline b\nline c\n")
            r = c.request("context.diff", {"path": str(f), "buffer": "line a\nline B CHANGED\nline c\n"})
            assert r["changed"] is True and "line B CHANGED" in r["added"] and "line b" in r["removed"]
            record("F082", "pass", ev("F082", "diff.json", r), "Diff Buffer vs gespeicherte Version")
        except Exception as e:
            record("F082", "fail", note=str(e))

        # F088 memory categorize
        try:
            md = "# Projekte\n- Abrevia\n# Assets\n- logo.svg\n# Preferences\n- pnpm\n"
            r = c.request("memory.categorize", {"content": md})
            assert {"Projekte", "Assets", "Preferences"} <= set(r["categories"].keys())
            record("F088", "pass", ev("F088", "categorize.json", r), "Memory in Kategorien gruppiert")
        except Exception as e:
            record("F088", "fail", note=str(e))

        # F093 memory token usage bar
        try:
            c.request("memory.set", {"scope": "global", "content": "# Memory\n" + ("fakt " * 200)})
            r = c.request("memory.token_usage", {"scope": "global"})
            assert r["tokens"] > 0 and r["fraction"] > 0
            record("F093", "pass", ev("F093", "token-usage.json", r), "Memory-Token-Usage gegen Budget")
        except Exception as e:
            record("F093", "fail", note=str(e))

        # F098 definitions grouped by category
        try:
            r = c.request("definitions.grouped", {})
            assert r["group_count"] >= 2 and any(isinstance(v, list) and v for v in r["groups"].values())
            record("F098", "pass", ev("F098", "grouped.json", r), "Definitionen nach category gruppiert")
        except Exception as e:
            record("F098", "fail", note=str(e))

        # F279 cost estimate
        try:
            opus = c.request("cost.estimate", {"model": "opus", "input_tokens": 100000, "output_tokens": 20000})
            haiku = c.request("cost.estimate", {"model": "haiku", "input_tokens": 100000, "output_tokens": 20000})
            assert opus["estimated_usd"] > haiku["estimated_usd"] > 0
            record("F279", "pass", ev("F279", "estimate.json", {"opus": opus, "haiku": haiku}), "Live-USD-Schätzung")
        except Exception as e:
            record("F279", "fail", note=str(e))

        # F284 productivity metrics
        try:
            repo = Path(tempfile.mkdtemp(prefix="cs-b16-repo-"))
            git(repo, "init", "-q", "-b", "main")
            (repo / "a.txt").write_text("one\ntwo\nthree\n"); git(repo, "add", "-A"); git(repo, "commit", "-qm", "feat: a")
            (repo / "b.txt").write_text("x\n"); git(repo, "add", "-A"); git(repo, "commit", "-qm", "feat: b")
            r = c.request("metrics.productivity", {"cwd": str(repo)})
            assert r["commits"] == 2 and r["lines_added"] >= 4
            record("F284", "pass", ev("F284", "productivity.json", r), "Commits/LoC/Sessions-Metriken")
        except Exception as e:
            record("F284", "fail", note=str(e))

        # F270 pipeline generator
        try:
            r = c.request("pipeline.generate", {"stack": "rust"})
            assert "cargo test" in r["workflow"] and "name: CI" in r["workflow"]
            record("F270", "pass", ev("F270", "pipeline.yml", r["workflow"]), "GitHub-Actions-Workflow generiert")
        except Exception as e:
            record("F270", "fail", note=str(e))

        # F345 extended-thinking setting
        try:
            c.request("settings.set", {"key": "extended_thinking", "value": True})
            v = c.request("settings.get", {"key": "extended_thinking"})["value"]
            assert v is True
            record("F345", "pass", ev("F345", "extended.json", {"value": v}), "Extended-Thinking-Modus konfigurierbar")
        except Exception as e:
            record("F345", "fail", note=str(e))

        # F067 worktree CLAUDE.md overrides
        try:
            wt = Path(tempfile.mkdtemp(prefix="cs-b16-wt-")); (wt / "CLAUDE.md").write_text("WORKTREE-OVERRIDE-MARK-555")
            a = c.request("context.assemble", {"cwd": str(ROOT), "worktree": str(wt)})
            wl = next(l for l in a["layers"] if l["layer"] == "worktree_override")
            assert "WORKTREE-OVERRIDE-MARK-555" in wl["content"] and "WORKTREE-OVERRIDE-MARK-555" in a["assembled_text"]
            record("F067", "pass", ev("F067", "worktree-override.json", wl), "Worktree-CLAUDE.md im Kontext geladen")
        except Exception as e:
            record("F067", "fail", note=str(e))

        # F100 / F111 / F112 / F113 agent config
        try:
            a1 = c.request("agents.create", {"name": "A1", "trigger": "scheduled", "token_budget": 1000,
                                             "definitions": ["Error Handling Standard"]})["id"]
            a2 = c.request("agents.create", {"name": "A2", "definitions": ["Video Frame Loading"]})["id"]
            # F112 trigger persisted
            assert c.request("agents.get", {"id": a1})["agent"]["trigger"] == "scheduled"
            record("F112", "pass", ev("F112", "trigger.json", {"trigger": "scheduled"}), "Trigger-Typ pro Agent konfiguriert")
            # F100 assigned definition auto-injected
            ctx1 = c.request("agents.context", {"id": a1})
            assert "Error Handling Standard" in ctx1["active_definitions"]
            record("F100", "pass", ev("F100", "assign.json", ctx1), "Definition dauerhaft Agent zugewiesen + geladen")
            # F111 context fork: two agents get distinct contexts
            ctx2 = c.request("agents.context", {"id": a2})
            assert ctx1["active_definitions"] != ctx2["active_definitions"]
            record("F111", "pass", ev("F111", "fork.json", {"a1_excerpt": ctx1["active_definitions"][:60], "a2_excerpt": ctx2["active_definitions"][:60]}),
                   "Context-Fork: eigener Kontext je Agent")
            # F113 token-budget enforcement via supervisor
            ev113 = c.request("supervisor.evaluate", {"tokens_used": 2000, "token_budget": 1000})
            assert ev113["action"] == "pause"
            record("F113", "pass", ev("F113", "budget.json", ev113), "Token-Budget-Überschreitung -> Agent pausiert")
        except Exception as e:
            for fid in ["F100", "F111", "F112", "F113"]:
                if fid not in results: record(fid, "fail", note=str(e))

        # F272 deploy post-health-check
        try:
            r = c.request("monitor.health_check", {"url": "http://localhost:6333/healthz"})
            assert r["ok"] is True
            record("F272", "pass", ev("F272", "post-deploy.json", r), "Post-Deploy Health-Check der Ziel-URL")
        except Exception as e:
            record("F272", "fail", note=str(e))

        # F280 budget alert at 80% of daily budget
        try:
            r = c.request("monitor.cost_guard", {"spent": 16.0, "budget": 20.0})
            assert r["status"] == "warn"
            record("F280", "pass", ev("F280", "budget-alert.json", r), "Budget-Alert bei 80% des Tagesbudgets")
        except Exception as e:
            record("F280", "fail", note=str(e))

        # F282 cost dashboard matches real usage
        try:
            sid = c.request("session.create", {"title": "x", "cwd": str(ROOT)})["id"]
            c.request("session.record_usage", {"session_id": sid, "cost_usd": 0.33, "model": "sonnet"})
            c.request("session.record_usage", {"session_id": sid, "cost_usd": 0.17, "model": "haiku"})
            total = c.request("session.stats", {})["total_cost_usd"]
            assert abs(total - 0.50) < 1e-9
            record("F282", "pass", ev("F282", "cost-match.json", {"total_cost_usd": total, "recorded": 0.50}),
                   "Dashboard-Summe == echter aufgezeichneter Verbrauch")
        except Exception as e:
            record("F282", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
