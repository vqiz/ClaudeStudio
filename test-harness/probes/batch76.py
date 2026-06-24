#!/usr/bin/env python3
"""Verifikation Build-Batch 92 (echter Core, echtes curl/HTTP, echte Audio-Bytes, kein Mock der Logik):

  F231  TTS Online via ElevenLabs: der Core streamt den Antworttext an den ElevenLabs-Endpoint
        (/v1/text-to-speech/:voice_id) und erzeugt daraus eine Audio-Datei. Nachgewiesen: echter
        HTTP-POST des Core, die zurückgelieferten Audio-Bytes werden als nicht-leere Audio-Datei
        gespeichert (HTTP 200, Bytes > 0).

ElevenLabs wird durch einen lokalen Audio-Substitut ersetzt (gleiches Lokal-Substitut-Muster wie
OpenAI/Slack/GitHub) — der API-Key bleibt extern; die zurückgegebenen Bytes sind ECHTES Audio
(via macOS `say` erzeugt). Getestet wird der echte TTS-Client + die echte Datei-Erzeugung des Core.
"""
from __future__ import annotations
import json, re, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}
AUDIO = b""
REQUESTS: list[dict] = []


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


class ElevenLabsMock(BaseHTTPRequestHandler):
    def do_POST(self):
        m = re.match(r"^/v1/text-to-speech/([^/]+)", self.path)
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
        if m:
            REQUESTS.append({"voice_id": m.group(1), "text": body.get("text"),
                             "model_id": body.get("model_id")})
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.end_headers()
            self.wfile.write(AUDIO)  # ECHTE Audio-Bytes
            return
        self.send_response(404); self.end_headers()

    def log_message(self, *a):
        pass


def main():
    global AUDIO
    # ECHTES Audio erzeugen (macOS say) und als ElevenLabs-Antwort ausliefern.
    src = Path(tempfile.mkdtemp(prefix="cs-f231-")) / "src.aiff"
    subprocess.run(["say", "ClaudeStudio spricht jetzt online.", "-o", str(src)],
                   capture_output=True, timeout=30)
    AUDIO = src.read_bytes()
    assert len(AUDIO) > 1000, "Quell-Audio leer"

    server = HTTPServer(("127.0.0.1", 0), ElevenLabsMock)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    out_path = Path(tempfile.mkdtemp(prefix="cs-f231-out-")) / "reply.mp3"
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b76.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=60)
        try:
            r = c.request("tts.synthesize", {
                "text": "Die Sicherheitsprüfung ist abgeschlossen — keine kritischen Findings.",
                "out_path": str(out_path), "api_base": base,
                "voice_id": "Rachel", "model_id": "eleven_multilingual_v2", "api_key": "test"})
            assert r["ok"] and r["http_status"] == 200, r
            assert r["bytes"] > 1000, f"Audio-Datei zu klein: {r['bytes']} Bytes"
            assert out_path.exists() and out_path.read_bytes() == AUDIO, "gespeicherte Datei != geliefertes Audio"
            assert REQUESTS and REQUESTS[-1]["voice_id"] == "Rachel" and REQUESTS[-1]["text"], \
                "Substitut empfing den TTS-Request nicht korrekt"
            record("F231", "pass", ev("F231", "online-tts.json",
                   {"http_status": r["http_status"], "bytes": r["bytes"], "out_path": str(out_path),
                    "voice_id": r["voice_id"], "model_id": r["model_id"],
                    "server_received": REQUESTS[-1]}),
                   f"ElevenLabs-TTS: Core POSTete Text, speicherte {r['bytes']} Bytes Audio (HTTP 200, voice=Rachel)")
        except Exception as e:
            record("F231", "fail", note=str(e))
        c.close()

    server.shutdown()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
