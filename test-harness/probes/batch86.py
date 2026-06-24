#!/usr/bin/env python3
"""Verifikation Build-Batch 108 (echter Core, ECHTES Whisper.cpp + openwakeword, kein Mock):

  F233  PTT-Modus (Push-to-Talk) als Alternative zum Wakeword: mit ptt=true überspringt die Voice-
        Pipeline das Wakeword-Gate (die Taste IST die Aktivierung) und verarbeitet das Audio direkt
        (STT → Intent) — OHNE Wakeword. Zum Kontrast: dasselbe Kommando-Audio OHNE Wakeword und OHNE
        PTT wird am Gate gestoppt. (Die Fn+Space-Geste ist – wie alle UI-Gesten – durch das ptt-Flag
        ersetzt; verifiziert wird das Pipeline-Verhalten der PTT-Aktivierung.)
"""
from __future__ import annotations
import json, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
WHISPER = "/opt/homebrew/bin/whisper-cli"
MODEL = ROOT / "test-harness" / "lib" / "whisper-models" / "ggml-base.bin"
PY = "/usr/bin/python3"
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
        record("F233", "fail", note="Whisper-Modell fehlt"); print(json.dumps({"results": results})); return
    tmp = Path(tempfile.mkdtemp(prefix="cs-f233-"))
    # Reines Kommando-Audio OHNE Wakeword
    sh("say", "change the background color to green", "-o", str(tmp / "cmd.aiff"))
    sh("afconvert", str(tmp / "cmd.aiff"), "-o", str(tmp / "cmd.wav"), "-f", "WAVE", "-d", "LEI16@16000")
    cmd = str(tmp / "cmd.wav")

    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/b86.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=120)
        try:
            # PTT: Wakeword-Gate übersprungen → direkt STT + Intent
            ptt = c.request("voice.pipeline",
                            {"audio": cmd, "model": str(MODEL), "whisper_bin": WHISPER,
                             "language": "en", "ptt": True})
            assert ptt["gated"] is False and ptt["activation"] == "push_to_talk", f"PTT nicht aktiviert: {ptt}"
            assert ptt["stages"] == ["push_to_talk", "stt", "intent"], f"Stufen falsch: {ptt['stages']}"
            assert "background" in ptt["transcript"].lower(), f"STT unerwartet: {ptt['transcript']!r}"
            assert ptt["intent"]["action"] == "set_background_color", f"Intent fehlt: {ptt['intent']}"

            # Kontrast: dasselbe Audio OHNE Wakeword + OHNE PTT → am Gate gestoppt
            gated = c.request("voice.pipeline",
                              {"audio": cmd, "model": str(MODEL), "whisper_bin": WHISPER,
                               "language": "en", "ptt": False, "python_bin": PY})
            assert gated["gated"] is True and gated["activation"] == "wakeword", f"Gate griff nicht: {gated}"

            record("F233", "pass", ev("F233", "ptt.json",
                   {"ptt_run": {"activation": ptt["activation"], "stages": ptt["stages"],
                                "transcript": ptt["transcript"], "intent": ptt["intent"]},
                    "gated_run": {"activation": gated["activation"], "gated": gated["gated"]}}),
                   f"PTT verarbeitet ohne Wakeword (→ {ptt['intent']['action']}); ohne PTT+ohne Wakeword am Gate gestoppt")
        except Exception as e:
            record("F233", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
