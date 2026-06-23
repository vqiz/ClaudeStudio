#!/usr/bin/env python3
"""Verifikation Build-Batch 24: Memory-Veraltet-Markierung (F092), Auto-Chunking (F175),
npm-test-Hook nach Write (F259). Echter Core, echtes npm, echtes embed_cli. Kein Mock.
"""
from __future__ import annotations
import json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}
DAY = 86_400_000


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b24.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=90)  # embed_cli batch can take ~10s

        # F092 memory staleness: >90 days -> stale, 10 days -> not
        try:
            now = 1_800_000_000_000
            r = c.request("memory.mark_stale", {"now_ms": now, "entries": [
                {"name": "alt", "last_used_ms": now - 100 * DAY},
                {"name": "frisch", "last_used_ms": now - 10 * DAY}]})
            by = {e["name"]: e["stale"] for e in r["entries"]}
            assert by["alt"] is True and by["frisch"] is False and r["stale_count"] == 1
            record("F092", "pass", ev("F092", "stale.json", r), "Eintrag >90 Tage als veraltet markiert, 10 Tage nicht")
        except Exception as e:
            record("F092", "fail", note=str(e))

        # F175 auto-chunking of a long transcript into ~300-token chunks
        try:
            sentence = "Der Agent integrierte die Stripe-Zahlung und schrieb Tests fuer die Rechnungslogik. "
            transcript = sentence * 200  # ~2800 words -> well over 2000 tokens
            r = c.request("knowledge.chunk_text", {"text": transcript, "chunk_tokens": 300, "collection": "sessions"})
            toks = [ch["tokens"] for ch in r["chunks"]]
            assert r["chunk_count"] >= 4
            assert all(250 <= t <= 360 for t in toks[:-1])  # interior chunks ~300 tokens
            # all chunks stored in the 'sessions' collection (retrievable from there)
            hits = c.request("knowledge.search", {"query": "Stripe Zahlung Rechnungslogik", "collection": "sessions", "top_k": 50})["hits"]
            assert all(h["collection"] == "sessions" for h in hits) and len(hits) >= r["chunk_count"]
            record("F175", "pass", ev("F175", "chunking.json", {"chunk_count": r["chunk_count"], "token_sizes": toks[:6], "stored_in_sessions": len(hits)}),
                   f"Transcript in {r['chunk_count']} ~300-Token-Chunks zerlegt + in 'sessions' gespeichert")
        except Exception as e:
            record("F175", "fail", note=str(e))

        # F259 npm-test-after-Write hook runs a real `npm test`
        try:
            proj = Path(tempfile.mkdtemp(prefix="cs-b24-npm-"))
            (proj / "package.json").write_text(json.dumps({"name": "demo", "version": "1.0.0",
                "scripts": {"test": "echo \"Tests: 3 passing\""}}))
            c.request("hooks.add", {"cwd": str(proj), "event": "PostToolUse", "matcher": "Write",
                                    "command": "npm test --silent"})
            (proj / "index.js").write_text("module.exports = 1\n")  # the "Write"
            run = c.request("hooks.run", {"cwd": str(proj), "event": "PostToolUse", "tool": "Write", "file": str(proj / "index.js")})
            out = run["fired"][0]["stdout"] if run["fired"] else ""
            assert "3 passing" in out and run["fired"][0]["exit"] == 0
            record("F259", "pass", ev("F259", "npm-test-hook.json", run),
                   "PostToolUse-Hook 'npm test' lief, Output mit bestandenen Tests im Log")
        except Exception as e:
            record("F259", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
