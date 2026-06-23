#!/usr/bin/env python3
"""Verifikation Build-Batch 39 (echter Core, Stub-`claude`, kein Mock):

  F160  Session-Replay Step-Through: session.replay_step navigiert deterministisch vor/zurück
        durch die geordneten Transcript-Schritte (has_prev/has_next korrekt, Inhalt stabil).
  F020  Filter-Chips: list.filter filtert eine Liste nach Kriterium und liefert den exakten
        'zeige N von M'-Zähler (12 von 240).
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
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b39.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": str(STUB)}) as ctx:
        c = P.Client(ctx["sock"], timeout=15)

        # F160 — geordnete Replay-Schritte mit Vor/Zurück-Navigation
        try:
            rid = str(uuid.uuid4())
            c.sock.sendall(P.encode_frame({"id": rid, "kind": "request", "method": "session.start",
                "payload": {"prompt": "Baue das Dashboard", "cwd": str(ROOT), "binary": str(STUB)}}))
            sid = None
            deadline = time.time() + 15
            while time.time() < deadline:
                f = c._read_frame()
                if f.get("id") == rid and f.get("kind") != "event":
                    sid = (f.get("payload") or {}).get("session_id"); continue
                if ((f.get("payload") or {}).get("event") or {}).get("kind") in ("result", "done"):
                    break
            assert sid
            s0 = c.request("session.replay_step", {"id": sid, "step": 0})
            total = s0["total"]
            assert total >= 2 and s0["index"] == 0 and s0["has_prev"] is False and s0["has_next"] is True
            assert s0["step"]["role"] == "user"
            # vorwärts durch alle Schritte (Rechts-Pfeil)
            forward = [c.request("session.replay_step", {"id": sid, "step": i})["step"]["content"]
                       for i in range(total)]
            last = c.request("session.replay_step", {"id": sid, "step": total - 1})
            assert last["has_next"] is False and last["has_prev"] is True
            # zurück zu Schritt 1 (Links-Pfeil) — Inhalt identisch zur Vorwärts-Sicht
            back1 = c.request("session.replay_step", {"id": sid, "step": 1})
            assert back1["step"]["content"] == forward[1]
            # über das Ende hinaus wird geklemmt (kein Crash)
            clamped = c.request("session.replay_step", {"id": sid, "step": 999})
            assert clamped["index"] == total - 1
            record("F160", "pass", ev("F160", "replay-step.json",
                   {"total": total, "step0": s0, "last": last, "forward_contents": forward}),
                   f"{total} geordnete Schritte; Vor/Zurück deterministisch (has_prev/has_next korrekt)")
        except Exception as e:
            record("F160", "fail", note=str(e))

        # F020 — Filter-Chip 'passing': exakt 12 von 240
        try:
            items = [{"id": i, "status": "passing" if i < 12 else "failing"} for i in range(240)]
            r = c.request("list.filter", {"items": items, "key": "status", "value": "passing"})
            assert r["total"] == 240 and r["visible"] == 12
            assert r["label"] == "zeige 12 von 240"
            assert len(r["matched"]) == 12 and all(m["status"] == "passing" for m in r["matched"])
            record("F020", "pass", ev("F020", "filter-count.json",
                   {"total": r["total"], "visible": r["visible"], "label": r["label"]}),
                   "Filter 'passing' -> Zähler 'zeige 12 von 240', genau 12 Treffer")
        except Exception as e:
            record("F020", "fail", note=str(e))

        c.close()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
