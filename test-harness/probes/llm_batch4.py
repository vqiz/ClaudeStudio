#!/usr/bin/env python3
"""Verifikation LLM-Batch 4 (echter Core, ECHTER claude):

  F215  Test-Tab führt den Task direkt aus und zeigt eine ECHTE Token-Anzahl + Dauer: tasks.test_run
        läuft mit `claude --output-format json` und liefert input/output_tokens (echte API-Usage) +
        duration_ms.
"""
from __future__ import annotations
import json, os, sys
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
    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/llm4.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": CLAUDE}) as ctx:
        c = P.Client(ctx["sock"], timeout=300)
        try:
            r = c.request("tasks.test_run", {
                "prompt": "Erkläre in zwei Sätzen, warum ein Health-Check-Endpoint nützlich ist."})
            assert r["ok"] is True, f"Lauf nicht ok: {r}"
            assert r["input_tokens"] > 0 and r["output_tokens"] > 0, f"keine echten Tokens: {r}"
            assert r["total_tokens"] == r["input_tokens"] + r["output_tokens"]
            assert isinstance(r["duration_ms"], (int, float)) and r["duration_ms"] > 0, f"keine Dauer: {r}"
            assert isinstance(r["result"], str) and r["result"].strip(), "kein Ergebnis-Text"
            record("F215", "pass", ev("F215", "task-test-run.json",
                   {"input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"],
                    "total_tokens": r["total_tokens"], "duration_ms": r["duration_ms"],
                    "cost_usd": r["cost_usd"], "result_preview": r["result"][:120]}),
                   f"echter Lauf: {r['total_tokens']} Tokens (in {r['input_tokens']}/out {r['output_tokens']}), {r['duration_ms']}ms")
        except Exception as e:
            record("F215", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
