#!/usr/bin/env python3
"""Verifikation Build-Batch 74 (echter Core, echter notify-rs-Watcher, echte Dateisystem-Änderungen):

  F062  notify-rs-Watcher synchronisiert live: ein gestarteter Watcher auf einem Verzeichnis erkennt
        Änderungen, die AUSSERHALB der App (per Python/Terminal) erfolgen — Anlegen, Ändern, Löschen —
        und rekursiv auch in Unterordnern. Die erkannten Events werden per files.watch_poll ausgelesen.
"""
from __future__ import annotations
import json, sys, tempfile, time
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
    watched = Path(tempfile.mkdtemp(prefix="cs-f062-"))
    (watched / "sub").mkdir()

    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b72.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=30)
        try:
            start = c.request("files.watch_start", {"path": str(watched)})
            wid = start["watch_id"]
            assert wid, "keine watch_id"
            time.sleep(0.6)  # Watcher registrieren lassen

            # Echte externe Änderungen (außerhalb der App):
            live = watched / "live.txt"
            live.write_text("hallo")                       # create
            time.sleep(0.3)
            live.write_text("hallo welt – geaendert")      # modify
            time.sleep(0.3)
            nested = watched / "sub" / "nested.txt"
            nested.write_text("rekursiv")                  # create (Unterordner -> rekursiv)
            time.sleep(0.3)
            live.unlink()                                  # remove

            # FSEvents hat Latenz/Koaleszenz -> bis zu ~6s pollen und akkumulieren.
            seen: list[dict] = []
            deadline = time.time() + 6
            while time.time() < deadline:
                time.sleep(0.5)
                poll = c.request("files.watch_poll", {"watch_id": wid})
                seen.extend(poll["events"])
                paths = " ".join(e["path"] for e in seen)
                kinds = {e["kind"] for e in seen}
                if "live.txt" in paths and "nested.txt" in paths and "remove" in kinds:
                    break

            paths = [e["path"] for e in seen]
            kinds = {e["kind"] for e in seen}
            joined = " ".join(paths)
            assert seen, "keine Änderungen erkannt"
            assert "live.txt" in joined, f"Anlegen/Ändern von live.txt nicht erkannt: {paths}"
            assert "nested.txt" in joined, f"rekursive Änderung (Unterordner) nicht erkannt: {paths}"
            assert "remove" in kinds, f"Löschen nicht erkannt (kinds={kinds})"
            assert {"create", "modify"} & kinds, f"kein create/modify erkannt (kinds={kinds})"

            stop = c.request("files.watch_stop", {"watch_id": wid})
            assert stop["ok"], "watch_stop fehlgeschlagen"

            record("F062", "pass", ev("F062", "live-watch.json",
                   {"watch_id": wid, "event_kinds": sorted(kinds),
                    "events": seen[:25], "event_count": len(seen), "stopped": stop["ok"]}),
                   f"Live-Watcher erkannte externe Änderungen (kinds={sorted(kinds)}, {len(seen)} Events), rekursiv + Löschen")
        except Exception as e:
            record("F062", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
