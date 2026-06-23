#!/usr/bin/env python3
"""Verifikation Build-Batch 44 (echter Core, Stub-`claude`, kein Mock):

  F346  Multi-Model-Vergleich: derselbe Prompt geht parallel an mehrere Modelle (je ein
        claude-Prozess), jede Antwort wird mit Modellname + Latenz erfasst (Nebeneinander-
        Spalten). Getestet wird die Dispatch-/Sammel-/Label-Mechanik, nicht die LLM-Qualität.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
STUB = ROOT / "test-harness" / "lib" / "stub_claude.sh"
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b44.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": str(STUB)}) as ctx:
        c = P.Client(ctx["sock"], timeout=30)
        try:
            r = c.request("models.compare", {
                "prompt": "Review: const q = `SELECT * FROM u WHERE id=${id}`",
                "models": ["claude-opus-4-8", "claude-haiku-4-5"],
                "binary": str(STUB)})
            resp = {x["model"]: x for x in r["responses"]}
            assert r["count"] == 2
            assert set(resp) == {"claude-opus-4-8", "claude-haiku-4-5"}
            for m, x in resp.items():
                assert x["response"].strip() != "", f"{m}: leere Antwort"
                assert x["latency_ms"] > 0, f"{m}: keine Latenz"
                assert "Schritt" in x["response"]  # echte geparste stream-json-Antwort
            record("F346", "pass", ev("F346", "multi-model.json",
                   {"models": r["models"],
                    "columns": [{"model": x["model"], "latency_ms": x["latency_ms"],
                                 "response_len": len(x["response"]), "cost_usd": x["cost_usd"]}
                                for x in r["responses"]]}),
                   "2 Modelle parallel: je Antwort + Modellname + Latenz (Nebeneinander-Spalten)")
        except Exception as e:
            record("F346", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
