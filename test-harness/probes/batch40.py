#!/usr/bin/env python3
"""Verifikation Build-Batch 40 (echter Core, kein Mock):

  F198  'Ausführen' stellt einen (vorgefertigten) Task in die persistente Agenten-Queue;
        queue.list zeigt ihn mit Status 'queued'.
  F214  Task-Scheduling unterstützt Manuell/Cron/Event/Threshold/Voice; Cron-Ausdruck wird
        validiert (5 Felder), ungültige Typen/Cron werden abgelehnt.
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
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b40.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=15)

        # F198 — Ausführen stellt den Task in die Agenten-Queue
        try:
            c.request("library.load_defaults", {})  # gelieferte Tasks in die Bibliothek laden
            tasks = c.request("tasks.list", {})["tasks"]
            assert tasks, "keine Tasks geladen"
            # ein vorgefertigter Compliance-Task
            name = next((t["name"] for t in tasks if t.get("category") == "compliance"), tasks[0]["name"])
            assert c.request("queue.list", {})["count"] == 0  # leer zu Beginn
            enq = c.request("queue.enqueue", {"task": name, "priority": "high"})
            assert enq["entry"]["status"] == "queued" and enq["entry"]["task"] == name
            c.request("queue.enqueue", {"task": "Zweiter Task"})
            q = c.request("queue.list", {})
            assert q["count"] == 2
            tasks_in_q = [e["task"] for e in q["queue"]]
            assert name in tasks_in_q and all(e["status"] == "queued" for e in q["queue"])
            record("F198", "pass", ev("F198", "enqueue.json", {"enqueued": enq["entry"], "queue": q}),
                   f"Task '{name[:24]}' in Agenten-Queue gestellt (Status queued); queue.list zeigt 2")
        except Exception as e:
            record("F198", "fail", note=str(e))

        # F214 — Scheduling: alle 5 Trigger-Typen + Cron-Validierung
        try:
            ok_types = {}
            for spec in (
                {"task": "audit", "type": "manual"},
                {"task": "audit", "type": "cron", "cron": "0 9 * * 1"},
                {"task": "audit", "type": "event", "event": "git.push"},
                {"task": "audit", "type": "threshold", "metric": "cost", "op": ">", "value": 10},
                {"task": "audit", "type": "voice", "phrase": "starte audit"},
            ):
                r = c.request("tasks.schedule", spec)
                ok_types[spec["type"]] = r["scheduled"]["type"]
            assert set(ok_types) == {"manual", "cron", "event", "threshold", "voice"}
            # Cron-Detail validiert (5 Felder)
            cron = c.request("tasks.schedule", {"task": "a", "type": "cron", "cron": "0 9 * * 1"})
            assert cron["scheduled"]["detail"]["valid"] and cron["scheduled"]["detail"]["fields"] == 5
            assert set(cron["supported_types"]) == {"manual", "cron", "event", "threshold", "voice"}
            # ungültiger Cron + unbekannter Typ -> Fehler
            bad_cron = False
            try:
                c.request("tasks.schedule", {"task": "a", "type": "cron", "cron": "nonsense"})
            except P.RemoteError:
                bad_cron = True
            bad_type = False
            try:
                c.request("tasks.schedule", {"task": "a", "type": "telepathy"})
            except P.RemoteError:
                bad_type = True
            assert bad_cron and bad_type
            record("F214", "pass", ev("F214", "schedule.json",
                   {"accepted_types": list(ok_types), "cron_detail": cron["scheduled"]["detail"],
                    "rejected_bad_cron": bad_cron, "rejected_bad_type": bad_type}),
                   "Manuell/Cron/Event/Threshold/Voice akzeptiert; Cron validiert; Ungültiges abgelehnt")
        except Exception as e:
            record("F214", "fail", note=str(e))

        c.close()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
