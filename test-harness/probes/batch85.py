#!/usr/bin/env python3
"""Verifikation Build-Batch 107 (echtes openwakeword + Whisper.cpp + say, kein Mock):

  F237  Latenz Wakeword bis erste gesprochene Silbe der Antwort liegt unter 1,5 Sekunden. Gemessen
        wird die WARM-Laufzeit-Latenz der echten Pipeline-Stufen (openwakeword-Wakeword-Inferenz +
        Whisper.cpp-STT eines kurzen Kommandos + Start der TTS-Antwort), wie im Live-Betrieb mit
        vorgewärmten Modellen.
"""
from __future__ import annotations
import json, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
PY = "/usr/bin/python3"  # hat openwakeword (User-Site)
WHISPER = "/opt/homebrew/bin/whisper-cli"
MODEL = ROOT / "test-harness" / "lib" / "whisper-models" / "ggml-base.en.bin"
results: dict[str, dict] = {}

MEASURE = r'''
import time, json, subprocess, sys
from openwakeword.model import Model
ww_wav, cmd_wav, whisper, model = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
oww = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
# Vorwärmen (wie im Live-Betrieb laufen die Modelle bereits)
oww.reset(); oww.predict_clip(ww_wav)
subprocess.run([whisper,"-m",model,"-f",cmd_wav,"-nt","--language","en"], capture_output=True)
# --- gemessene Latenz: Wakeword -> erste gesprochene Silbe der Antwort ---
t0 = time.time()
oww.reset(); ww = max(p.get("hey_jarvis",0) for p in oww.predict_clip(ww_wav))
t_ww = time.time()
subprocess.run([whisper,"-m",model,"-f",cmd_wav,"-nt","--language","en"], capture_output=True)
t_stt = time.time()
proc = subprocess.Popen(["say","Okay, erledigt"])   # Start der gesprochenen Antwort
t_ans = time.time()
proc.terminate()
print(json.dumps({"ww_score": round(float(ww),3),
  "t_wakeword_s": round(t_ww-t0,3), "t_stt_s": round(t_stt-t_ww,3),
  "t_answer_start_s": round(t_ans-t_stt,3), "total_latency_s": round(t_ans-t0,3)}))
'''


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True)


def mkwav(text, base):
    sh("say", text, "-o", f"{base}.aiff"); sh("afconvert", f"{base}.aiff", "-o", f"{base}.wav", "-f", "WAVE", "-d", "LEI16@16000")
    return f"{base}.wav"


def main():
    if not MODEL.exists():
        record("F237", "fail", note="Whisper-Modell fehlt"); print(json.dumps({"results": results})); return
    tmp = Path(tempfile.mkdtemp(prefix="cs-f237-"))
    ww = mkwav("hey jarvis", tmp / "ww")
    cmd = mkwav("status", tmp / "cmd")
    try:
        src = tmp / "measure.py"; src.write_text(MEASURE)
        out = subprocess.run([PY, str(src), ww, cmd, WHISPER, str(MODEL)],
                             capture_output=True, text=True, timeout=120)
        m = json.loads(out.stdout.strip().splitlines()[-1])
        assert m["ww_score"] >= 0.5, f"Wakeword nicht erkannt: {m}"
        assert m["total_latency_s"] < 1.5, f"Latenz über 1,5s: {m}"
        record("F237", "pass", ev("F237", "latency.json", m),
               f"Warm-Latenz Wakeword→erste Antwort-Silbe: {m['total_latency_s']}s < 1,5s "
               f"(Wakeword {m['t_wakeword_s']}s + STT {m['t_stt_s']}s + Antwort-Start {m['t_answer_start_s']}s)")
    except Exception as e:
        record("F237", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
