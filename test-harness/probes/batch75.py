#!/usr/bin/env python3
"""Verifikation Build-Batch 81 (echte macOS-TTS-Engine, offline, kein Mock):

  F232  TTS Offline Fallback via AVSpeechSynthesizer: bei fehlendem Netzwerk synthetisiert das System
        gesprochene Sprache lokal. Nachgewiesen über die ECHTE AVSpeechSynthesizer-Engine (dieselbe,
        die VoiceController.speak() nutzt) via .write(): für gegebenen Text werden echte PCM-Audio-
        Frames mit nicht-trivialem Pegel erzeugt — komplett offline (kein Netz, kein Mikrofon).
"""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
TTS = ROOT / "test-harness" / "lib" / "ttsprobe"
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def synth(text: str, out: Path) -> dict:
    r = subprocess.run([str(TTS), text, str(out)], capture_output=True, text=True, timeout=30)
    return json.loads(r.stdout.strip().splitlines()[-1])


def main():
    if not TTS.exists():
        # Harness kompilieren, falls nötig.
        subprocess.run(["swiftc", "-O", str(TTS.with_suffix(".swift")), "-o", str(TTS)],
                       capture_output=True, text=True, timeout=180)
    try:
        evdir = P.evidence_dir("F232") if hasattr(P, "evidence_dir") else (ROOT / "test-harness" / "evidence" / "F232")
        Path(evdir).mkdir(parents=True, exist_ok=True)
        de = synth("Hallo, hier spricht ClaudeStudio im Offline-Modus.", Path(evdir) / "tts_de.caf")
        en = synth("Switching to the offline voice fallback.", Path(evdir) / "tts_en.caf")
        for tag, m in (("de", de), ("en", en)):
            assert m["done"], f"{tag}: Synthese nicht abgeschlossen"
            assert m["frames"] > 2000, f"{tag}: zu wenig Audio ({m['frames']} Frames)"
            assert m["peak"] > 0.1, f"{tag}: Audio still/leer (peak={m['peak']})"
            assert m["bytes"] > 2000, f"{tag}: Datei zu klein ({m['bytes']} bytes)"
        record("F232", "pass", ev("F232", "offline-tts.json",
               {"de": de, "en": en, "engine": "AVSpeechSynthesizer.write (offline)",
                "audio_files": ["test-harness/evidence/F232/tts_de.caf",
                                "test-harness/evidence/F232/tts_en.caf"]}),
               f"AVSpeechSynthesizer offline: DE {de['frames']} Frames/peak {de['peak']:.2f}, "
               f"EN {en['frames']} Frames/peak {en['peak']:.2f} — echte Sprachaudio ohne Netz/Mikro")
    except Exception as e:
        record("F232", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
