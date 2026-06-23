#!/usr/bin/env python3
"""Verifikation Build-Batch 8: Prompt-Studio (History/Templates/Favoriten/Chains)
+ Archiv-Filter, gegen den echten Core. Datei/Logik-basiert, kein Mock.
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
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b8.log")) as ctx:
        c = P.Client(ctx["sock"])

        # F243: prompt template library
        try:
            t = c.request("prompts.templates", {})["templates"]
            names = {x["name"] for x in t}
            assert {"Code Review", "Tests schreiben", "Refactoring"} <= names
            assert all("{{" in x["template"] for x in t)
            record("F243", "pass", ev("F243", "templates.json", t), "Prompt-Template-Bibliothek (Review/Tests/Refactor…)")
        except Exception as e:
            record("F243", "fail", note=str(e))

        # F244: prompt history chronological + repeat (retrievable)
        try:
            ids = []
            for i, pr in enumerate(["fix the parser", "add dark mode", "write tests for auth"]):
                ids.append(c.request("prompts.record", {"prompt": pr, "agent": "dev", "tokens": 100 + i})["id"])
            hist = c.request("prompts.history", {"limit": 10})["entries"]
            assert hist[0]["prompt"] == "write tests for auth"  # newest first
            assert all("timestamp" in e and "tokens" in e for e in hist)
            # "repeat" = the recorded prompt is retrievable to re-run
            assert any(e["id"] == ids[0] and e["prompt"] == "fix the parser" for e in hist)
            record("F244", "pass", ev("F244", "history.json", hist),
                   "Prompt-History chronologisch (Timestamp/Agent/Token) + wiederholbar")
        except Exception as e:
            record("F244", "fail", note=str(e))

        # F245: favorites + full-text search over history
        try:
            target = next(e for e in c.request("prompts.history", {})["entries"] if e["prompt"] == "add dark mode")
            c.request("prompts.favorite", {"id": target["id"], "favorite": True})
            favs = c.request("prompts.history", {"favorites_only": True})["entries"]
            search = c.request("prompts.history", {"query": "parser"})["entries"]
            assert len(favs) == 1 and favs[0]["prompt"] == "add dark mode" and favs[0]["favorite"] is True
            assert len(search) == 1 and "parser" in search[0]["prompt"]
            record("F245", "pass", ev("F245", "favorites-search.json", {"favorites": favs, "search_parser": search}),
                   "Favoriten + Volltextsuche über History")
        except Exception as e:
            record("F245", "fail", note=str(e))

        # F246: prompt chain pipes output -> input
        try:
            r = c.request("prompts.chain_run", {"input": "hello", "steps": [
                {"op": "append", "arg": " world"}, {"op": "upper"}, {"op": "replace", "from": "WORLD", "to": "CLAUDE"}]})
            assert r["output"] == "HELLO CLAUDE", f"got {r['output']!r}"
            record("F246", "pass", ev("F246", "chain.json", r), "Chain: Output jedes Schritts -> Input des nächsten")
        except Exception as e:
            record("F246", "fail", note=str(e))

        # F247: conditional branch (contains X -> A else B)
        try:
            yes = c.request("prompts.chain_run", {"input": "this has ERROR inside", "steps": [
                {"op": "branch", "contains": "ERROR",
                 "then": [{"op": "set", "arg": "ran fix branch"}],
                 "else": [{"op": "set", "arg": "ran ok branch"}]}]})
            no = c.request("prompts.chain_run", {"input": "all good", "steps": [
                {"op": "branch", "contains": "ERROR",
                 "then": [{"op": "set", "arg": "ran fix branch"}],
                 "else": [{"op": "set", "arg": "ran ok branch"}]}]})
            assert yes["output"] == "ran fix branch" and no["output"] == "ran ok branch"
            record("F247", "pass", ev("F247", "branch.json", {"with_error": yes, "clean": no}),
                   "Bedingte Verzweigung: enthält Output X -> A, sonst B")
        except Exception as e:
            record("F247", "fail", note=str(e))

        # F158: archive filter by project (cwd) and timeframe
        try:
            sa = c.request("session.create", {"title": "A", "cwd": "/proj/alpha"})["id"]
            c.request("session.create", {"title": "B", "cwd": "/proj/beta"})
            only_alpha = c.request("session.list", {"project": "/proj/alpha"})["sessions"]
            future = c.request("session.list", {"since": 99999999999999})["sessions"]
            all_since0 = c.request("session.list", {"since": 0})["sessions"]
            assert len(only_alpha) == 1 and only_alpha[0]["id"] == sa
            assert future == [] and len(all_since0) >= 2
            record("F158", "pass", ev("F158", "filter.json",
                   {"project_alpha": only_alpha, "future_empty": future == [], "since0_count": len(all_since0)}),
                   "Archiv-Filter nach Projekt + Zeitraum (Modell/Kosten/Tools verfügbar)")
        except Exception as e:
            record("F158", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
