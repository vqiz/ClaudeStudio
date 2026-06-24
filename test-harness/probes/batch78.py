#!/usr/bin/env python3
"""Verifikation Build-Batch 99 (echter Core, ECHTES whisper.cpp, offline, kein Mock):

  F229  STT Offline via Whisper.cpp lokal: eine Audiodatei wird OHNE Netzwerk auf dem Gerät
        transkribiert. Nachgewiesen: der Core ruft `whisper-cli` mit einem lokalen GGML-Modell auf
        und transkribiert ein per macOS `say` erzeugtes 16-kHz-WAV korrekt — kein Mikrofon, kein Netz.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
WHISPER = "/opt/homebrew/bin/whisper-cli"
MODEL = ROOT / "test-harness" / "lib" / "whisper-models" / "ggml-base.en.bin"
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def main():
    if not MODEL.exists():
        MODEL.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["curl", "-sL",
                        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin",
                        "-o", str(MODEL)], timeout=300)

    sentence = "the security review found three issues in the payment module"
    tmp = Path(tempfile.mkdtemp(prefix="cs-f229-"))
    aiff, wav = tmp / "v.aiff", tmp / "v.wav"
    subprocess.run(["say", sentence, "-o", str(aiff)], capture_output=True, timeout=30)
    subprocess.run(["afconvert", str(aiff), "-o", str(wav), "-f", "WAVE", "-d", "LEI16@16000"],
                   capture_output=True, timeout=30)
    assert wav.exists() and wav.stat().st_size > 1000, "WAV nicht erzeugt"

    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b78.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=120)
        try:
            r = c.request("stt.transcribe",
                          {"audio": str(wav), "model": str(MODEL), "whisper_bin": WHISPER})
            assert r["ok"] and r["offline"] is True and r["engine"] == "whisper.cpp", r
            transcript = r["transcript"].lower()
            # Wort-Übereinstimmung mit dem bekannten Satz
            ref = set(sentence.split())
            got = set("".join(ch for ch in transcript if ch.isalnum() or ch.isspace()).split())
            overlap = len(ref & got) / len(ref)
            assert overlap >= 0.8, f"Transkript weicht zu stark ab ({overlap:.0%}): {transcript!r}"
            record("F229", "pass", ev("F229", "whisper-stt.json",
                   {"reference": sentence, "transcript": r["transcript"], "word_overlap": round(overlap, 2),
                    "engine": r["engine"], "offline": r["offline"], "model": "ggml-base.en.bin"}),
                   f"whisper.cpp offline: '{r['transcript'].strip()}' ({overlap:.0%} Wort-Übereinstimmung), kein Netz/Mikro")
        except Exception as e:
            record("F229", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
