#!/usr/bin/env python3
"""Verifikation Build-Batch 105 (echter Core, Stub-`claude` mit Token-Streaming, kein Mock):

  F136  Echtzeit-Session-Panel zeigt Live-Output einer laufenden Agent-Session: die Streaming-
        Antwort erscheint ZEICHENWEISE (Token für Token), nicht erst am Ende komplett. Nachgewiesen
        über die session.event-Frames (assistant_delta), die INKREMENTELL über die Zeit eintreffen
        (Ankunfts-Zeitstempel) und sich zum vollständigen Text zusammensetzen.
"""
from __future__ import annotations
import json, sys, time, uuid
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
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b83.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": str(STUB)}) as ctx:
        ca = P.Client(ctx["sock"], timeout=15)
        try:
            t0 = time.time()
            rid = str(uuid.uuid4())
            ca.sock.sendall(P.encode_frame({"id": rid, "kind": "request", "method": "session.start",
                "payload": {"prompt": "STREAM Füge einen DELETE-Endpoint hinzu", "cwd": str(ROOT),
                            "binary": str(STUB)}}))
            deltas = []  # (relative_arrival_time, text)
            deadline = time.time() + 10
            while time.time() < deadline:
                f = ca._read_frame()
                evd = ((f.get("payload") or {}).get("event") or {})
                k = evd.get("kind")
                if k == "assistant_delta":
                    deltas.append((round(time.time() - t0, 3), evd.get("text", "")))
                if k in ("result", "done", "stopped"):
                    break

            assert len(deltas) >= 4, f"zu wenige Stream-Tokens: {deltas}"
            full = "".join(t for _, t in deltas)
            assert "delete" in full.lower() and "endpoint" in full.lower(), f"Text unerwartet: {full!r}"
            # INKREMENTELL: die Tokens treffen über die Zeit verteilt ein, nicht alle gleichzeitig.
            arrivals = [t for t, _ in deltas]
            span = arrivals[-1] - arrivals[0]
            gaps = [round(arrivals[i + 1] - arrivals[i], 3) for i in range(len(arrivals) - 1)]
            assert span > 0.6, f"Tokens kamen nicht zeitlich verteilt (Spanne {span}s)"
            assert sum(1 for g in gaps if g > 0.1) >= 3, f"keine echten Pausen zwischen Tokens: {gaps}"

            record("F136", "pass", ev("F136", "live-stream-tokens.json",
                   {"token_count": len(deltas), "full_text": full,
                    "arrivals_s": arrivals, "gaps_s": gaps, "span_s": round(span, 3),
                    "ipc_deltas": [{"t": t, "text": tx} for t, tx in deltas]}),
                   f"{len(deltas)} Tokens zeichenweise über {span:.2f}s eingetroffen → '{full}' (nicht erst am Ende)")
        except Exception as e:
            record("F136", "fail", note=str(e))
        ca.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
