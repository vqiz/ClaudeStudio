#!/usr/bin/env python3
"""Verifikation Build-Batch 102 (echter Core, ECHTES Whisper.cpp, echtes Event-Log, kein Mock):

  F238  Voice-Aktion startet einen Task: der gesprochene Befehl 'Starte den Steuer-Check' löst die
        Ausführung eines Tasks aus. Kette: macOS `say` (deutsche Stimme) → Core voice.run_command
        (Whisper.cpp-STT + Intent → start_task) → ein Task-Start-Event wird ins Event-Log geschrieben
        (echte Ausführungs-Auslösung). Per Response + Event-Log nachgewiesen.
"""
from __future__ import annotations
import json, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
WHISPER = "/opt/homebrew/bin/whisper-cli"
MODEL = ROOT / "test-harness" / "lib" / "whisper-models" / "ggml-base.bin"
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True)


def main():
    if not MODEL.exists():
        record("F238", "fail", note="Whisper-Modell fehlt"); print(json.dumps({"results": results})); return

    tmp = Path(tempfile.mkdtemp(prefix="cs-f238-"))
    aiff, wav = tmp / "v.aiff", tmp / "v.wav"
    cmd = "Starte den Steuer-Check"
    if sh("say", "-v", "Anna", cmd, "-o", str(aiff)).returncode != 0:
        sh("say", cmd, "-o", str(aiff))
    sh("afconvert", str(aiff), "-o", str(wav), "-f", "WAVE", "-d", "LEI16@16000")

    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b80.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=120)
        home = ctx["home"]
        try:
            r = c.request("voice.run_command",
                          {"audio": str(wav), "model": str(MODEL), "whisper_bin": WHISPER, "language": "de"})
            tlc = r["transcript"].lower()
            assert r["ok"] and r["action"] == "start_task", f"kein start_task-Intent: {r}"
            assert r["executed"] is True and r.get("task"), f"Task nicht ausgelöst: {r}"
            assert "starte" in tlc or "startet" in tlc, f"kein Start-Verb im Transkript: {r['transcript']!r}"
            assert r["event"]["kind"] == "voice_task_started", f"kein Event in Response: {r}"

            # Echter Effekt: das Event-Log enthält den Task-Start.
            logs = list(Path(home).glob("**/event_log.jsonl"))
            assert logs, "kein event_log.jsonl gefunden"
            entries = [json.loads(l) for l in logs[0].read_text().splitlines() if l.strip()]
            started = [e for e in entries if e.get("kind") == "voice_task_started"]
            assert started and started[-1]["task"] == r["task"], f"Task-Start nicht im Event-Log: {entries[-3:]}"

            record("F238", "pass", ev("F238", "voice-task-start.json",
                   {"transcript": r["transcript"], "action": r["action"], "task": r["task"],
                    "executed": r["executed"], "event_log_entry": started[-1]}),
                   f"Sprachbefehl '{r['transcript'].strip()}' → start_task '{r['task']}', Event ins Log geschrieben")
        except Exception as e:
            record("F238", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
