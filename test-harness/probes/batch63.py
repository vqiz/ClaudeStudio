#!/usr/bin/env python3
"""Verifikation Build-Batch 63 (echter Core, kein Mock):

  F150  Session-Notiz: eine Notiz wird zu einer Session hinzugefügt, gespeichert; nach App-Neustart
        (neuer Core-Prozess, gleiches HOME) ist die Notiz noch da.
"""
from __future__ import annotations
import json, sys, tempfile
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
    home = Path(tempfile.mkdtemp(prefix="cs-f150-home-"))
    note_text = "Review noetig vor Merge"
    try:
        # Lauf 1: Session anlegen + Notiz setzen
        with P.running_core(home=home, library_dir=ROOT, log_path=Path("/tmp/b63a.log")) as ctx:
            c = P.Client(ctx["sock"], timeout=30)
            sid = c.request("session.create", {"title": "Auth-Refactor", "cwd": str(ROOT)}).get("id") \
                or c.request("session.create", {"title": "Auth-Refactor", "cwd": str(ROOT)}).get("session_id")
            assert sid, "keine session_id von session.create"
            c.request("session.set_note", {"session_id": sid, "note": note_text})
            # direkt lesbar
            assert c.request("session.get_note", {"session_id": sid})["note"] == note_text
            c.close()

        # Lauf 2: NEUER Core-Prozess, gleiches HOME -> Notiz muss persistiert sein
        with P.running_core(home=home, library_dir=ROOT, log_path=Path("/tmp/b63b.log")) as ctx:
            c = P.Client(ctx["sock"], timeout=30)
            got = c.request("session.get_note", {"session_id": sid})["note"]
            assert got == note_text, f"Notiz nach Neustart verloren: {got!r}"
            c.close()
        record("F150", "pass", ev("F150", "session-note.json",
               {"session_id": sid, "note": note_text, "persisted_across_restart": True}),
               "Session-Notiz überlebte App-Neustart (neuer Core, gleiches HOME)")
    except Exception as e:
        record("F150", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
