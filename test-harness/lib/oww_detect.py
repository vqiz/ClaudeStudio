#!/usr/bin/env python3
"""Lokale Wakeword-Erkennung via openwakeword (F227/F226). Liest einen 16-kHz-WAV-Pfad,
führt das vortrainierte 'hey_jarvis'-Modell darüber aus und gibt den Maximal-Score +
Trigger-Entscheidung als JSON auf stdout aus. Komplett offline (ONNX-Modell lokal)."""
import json
import sys

from openwakeword.model import Model

WAKEWORD = "hey_jarvis"
THRESHOLD = 0.5


def main() -> None:
    audio = sys.argv[1]
    threshold = float(sys.argv[2]) if len(sys.argv) > 2 else THRESHOLD
    model = Model(wakeword_models=[WAKEWORD], inference_framework="onnx")
    preds = model.predict_clip(audio)
    score = float(max(p.get(WAKEWORD, 0.0) for p in preds))
    print(json.dumps({"wakeword": WAKEWORD, "score": round(score, 4),
                      "triggered": score >= threshold, "threshold": threshold}))


if __name__ == "__main__":
    main()
