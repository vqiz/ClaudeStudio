#!/usr/bin/env python3
"""Verifikation Build-Batch 106 (echter Core, echte `say`-TTS, echtes SIGTERM, kein Mock):

  F234  Barge-In: ein neues Kommando unterbricht eine laufende TTS-Ausgabe SOFORT. Eine lange TTS-
        Ausgabe (`say`, ~mehrere Sekunden) wird gestartet; mitten in der Wiedergabe ruft ein neues
        Kommando voice.barge_in → der TTS-Prozess wird sofort terminiert (Wiedergabe bricht ab, lange
        vor dem natürlichen Ende).
"""
from __future__ import annotations
import json, subprocess, sys, time
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


def alive(pid):
    return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                          capture_output=True, text=True).stdout.strip()


def main():
    LONG = ("Dies ist eine sehr lange gesprochene Antwort die mehrere Sekunden dauert und "
            "genug Zeit lässt um sie mitten in der Wiedergabe durch ein neues Kommando zu unterbrechen.")
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b84.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=30)
        try:
            t0 = time.time()
            play = c.request("voice.tts_play", {"text": LONG})
            assert play["playing"] is True and play["tts_pid"], play
            pid = play["tts_pid"]
            time.sleep(0.6)
            st = alive(pid)
            assert st and "Z" not in st, f"TTS-Prozess (PID {pid}) spielt nicht (Zustand {st!r})"

            # Neues Kommando → Barge-In mitten in der Wiedergabe
            barge = c.request("voice.barge_in", {"tts_pid": pid, "new_command": "Stopp, etwas anderes"})
            elapsed = time.time() - t0
            assert barge["interrupted"] is True and barge["was_playing"] is True, barge
            time.sleep(0.4)
            st2 = alive(pid)
            assert not st2 or "Z" in st2, f"TTS-Prozess nach Barge-In noch aktiv (Zustand {st2!r})"
            assert elapsed < 2.0, f"Barge-In nicht 'sofort' ({elapsed:.2f}s)"

            record("F234", "pass", ev("F234", "barge-in.json",
                   {"tts_pid": pid, "state_while_playing": st, "interrupted_after_s": round(elapsed, 2),
                    "state_after_barge_in": st2 or "DEAD", "barge": barge}),
                   f"Barge-In: TTS (PID {pid}, Zustand {st}) nach {elapsed:.2f}s sofort unterbrochen → Prozess beendet")
        except Exception as e:
            record("F234", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
