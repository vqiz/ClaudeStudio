#!/usr/bin/env python3
"""Verifikation Build-Batch 103 (echter Core, ECHTES openwakeword + Whisper.cpp, offline, kein Mock):

  F227  Wakeword-Erkennung mit openwakeword lokal: das Modell triggert beim echten Wakeword
        ('hey jarvis'), aber NICHT bei anderem Sprach-Audio.
  F226  Voice-Pipeline End-to-End: Audio → Wakeword → STT → Intent-Parser. Bei getriggertem
        Wakeword durchläuft das Audio alle Stufen und liefert einen Intent; ohne Wakeword wird die
        Pipeline am Gate gestoppt.
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


def mkwav(text, base: Path) -> Path:
    aiff, wav = base.with_suffix(".aiff"), base.with_suffix(".wav")
    sh("say", text, "-o", str(aiff))
    sh("afconvert", str(aiff), "-o", str(wav), "-f", "WAVE", "-d", "LEI16@16000")
    return wav


def main():
    if not MODEL.exists():
        record("F226", "fail", note="Whisper-Modell fehlt"); record("F227", "fail", note="Modell fehlt")
        print(json.dumps({"results": results})); return

    tmp = Path(tempfile.mkdtemp(prefix="cs-vp-"))
    ww = mkwav("hey jarvis", tmp / "ww")
    nw = mkwav("the weather is nice today", tmp / "nw")
    pipe = mkwav("hey jarvis change the background color to red", tmp / "pipe")

    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/b81.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=180)
        # ---- F227: Wakeword nur beim echten Wort ----
        try:
            t = c.request("voice.detect_wakeword", {"audio": str(ww), "python_bin": "/usr/bin/python3"})
            n = c.request("voice.detect_wakeword", {"audio": str(nw), "python_bin": "/usr/bin/python3"})
            assert t["triggered"] is True and t["score"] >= 0.5, f"Wakeword nicht erkannt: {t}"
            assert n["triggered"] is False and n["score"] < 0.5, f"Falsch-Trigger bei Nicht-Wakeword: {n}"
            record("F227", "pass", ev("F227", "wakeword.json",
                   {"wakeword_audio": t, "nonword_audio": n}),
                   f"openwakeword: 'hey jarvis' Score {t['score']:.3f} (Trigger), anderes Audio {n['score']:.3f} (kein Trigger)")
        except Exception as e:
            record("F227", "fail", note=str(e))

        # ---- F226: E2E-Pipeline ----
        try:
            full = c.request("voice.pipeline",
                             {"audio": str(pipe), "model": str(MODEL), "whisper_bin": WHISPER, "language": "en", "python_bin": "/usr/bin/python3"})
            assert full["gated"] is False and full["stages"] == ["wakeword", "stt", "intent"], full
            assert full["wakeword"]["triggered"] is True, "Wakeword-Stufe nicht getriggert"
            assert "background" in full["transcript"].lower(), f"STT-Stufe unerwartet: {full['transcript']!r}"
            assert full["intent"]["action"] == "set_background_color" and full["intent"]["color"] == "red", \
                f"Intent-Stufe falsch: {full['intent']}"
            # Ohne Wakeword: Pipeline am Gate gestoppt
            gated = c.request("voice.pipeline",
                              {"audio": str(nw), "model": str(MODEL), "whisper_bin": WHISPER, "language": "en", "python_bin": "/usr/bin/python3"})
            assert gated["gated"] is True and gated["stages"] == ["wakeword"], f"Gate griff nicht: {gated}"
            record("F226", "pass", ev("F226", "voice-pipeline.json",
                   {"triggered_run": {"stages": full["stages"], "transcript": full["transcript"],
                                      "intent": full["intent"], "wakeword_score": full["wakeword"]["score"]},
                    "gated_run": {"stages": gated["stages"], "gated": gated["gated"]}}),
                   f"E2E: Wakeword→STT→Intent ({full['intent']['action']}={full['intent'].get('color')}); ohne Wakeword am Gate gestoppt")
        except Exception as e:
            record("F226", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
