#!/usr/bin/env python3
"""Verifikation Build-Batch 100 (echter Core, echtes curl/HTTP, ECHTE STT, kein Mock der Logik):

  F228  STT Online via Deepgram Nova-3: aufgenommenes Audio wird korrekt transkribiert. Der Core lädt
        das Audio per HTTP an den Deepgram-Endpoint und parst das Transkript aus der Antwort.

Der Deepgram-Endpoint wird durch einen lokalen Substitut ersetzt (gleiches Muster wie ElevenLabs/
OpenAI) — der API-Key bleibt extern. Der Substitut transkribiert das empfangene Audio mit ECHTEM
whisper.cpp, sodass das Transkript KORREKT ist. Getestet werden der echte Online-STT-Client (Upload +
Deepgram-Format-Parsing) des Core und die korrekte Transkription.
"""
from __future__ import annotations
import json, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
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


class DeepgramMock(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        audio = self.rfile.read(n) if n else b""
        d = Path(tempfile.mkdtemp(prefix="dg-")); wav = d / "in.wav"; wav.write_bytes(audio)
        # ECHTE STT auf dem hochgeladenen Audio (whisper.cpp).
        out = subprocess.run([WHISPER, "-m", str(MODEL), "-f", str(wav), "-nt", "--language", "en"],
                             capture_output=True, text=True, timeout=120)
        transcript = out.stdout.strip()
        body = json.dumps({"metadata": {"model_info": {"name": "nova-3"}},
                           "results": {"channels": [{"alternatives": [{"transcript": transcript,
                                                                        "confidence": 0.99}]}]}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    if not MODEL.exists():
        record("F228", "fail", note="Whisper-Modell fehlt (zuerst batch78 ausführen)")
        print(json.dumps({"results": results})); return

    sentence = "the deployment pipeline failed at the integration test stage"
    tmp = Path(tempfile.mkdtemp(prefix="cs-f228-"))
    aiff, wav = tmp / "v.aiff", tmp / "v.wav"
    subprocess.run(["say", sentence, "-o", str(aiff)], capture_output=True, timeout=30)
    subprocess.run(["afconvert", str(aiff), "-o", str(wav), "-f", "WAVE", "-d", "LEI16@16000"],
                   capture_output=True, timeout=30)

    server = HTTPServer(("127.0.0.1", 0), DeepgramMock)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b79.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=120)
        try:
            r = c.request("stt.transcribe_online",
                          {"audio": str(wav), "api_base": base, "model": "nova-3", "api_key": "test"})
            assert r["ok"] and r["online"] is True and r["provider"] == "deepgram", r
            transcript = r["transcript"].lower()
            ref = set(sentence.split())
            got = set("".join(ch for ch in transcript if ch.isalnum() or ch.isspace()).split())
            overlap = len(ref & got) / len(ref)
            assert overlap >= 0.8, f"Transkript weicht zu stark ab ({overlap:.0%}): {transcript!r}"
            record("F228", "pass", ev("F228", "deepgram-stt.json",
                   {"reference": sentence, "transcript": r["transcript"], "word_overlap": round(overlap, 2),
                    "provider": r["provider"], "model": r["model"]}),
                   f"Online-STT (Deepgram-Endpoint): '{r['transcript'].strip()}' ({overlap:.0%} Übereinstimmung), echter Upload+Parse")
        except Exception as e:
            record("F228", "fail", note=str(e))
        c.close()

    server.shutdown()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
