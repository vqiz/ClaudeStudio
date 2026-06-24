#!/usr/bin/env python3
"""Verifikation UI-Batch 25 (echte SwiftUI-View via ImageRenderer, kein Mock):

  F033  Cmd+/ öffnet ein Shortcut-Overlay, das die verfügbaren Tastenkürzel auflistet (Esc schließt).
        Das echte RootView bindet Cmd+/ an das Umschalten des ShortcutOverlay
        (keyboardShortcut("/", .command), siehe RootView.swift). Der Overlay-INHALT wird via
        ImageRenderer (Fenster-Server-unabhängig) in eine PNG gerendert und per OCR verifiziert:
        Titel + alle Tastenkürzel-Einträge. Die Cmd+/-Geste ist – wie bei allen UI-Features – durch
        den Render-Seam ersetzt; verifiziert werden Overlay-Inhalt + die deklarierte Tastenbindung.
        Evidence: test-harness/evidence/F033-overlay.png.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
EVID = ROOT / "test-harness" / "evidence"
APP = ROOT / "app" / ".build" / "debug" / "ClaudeStudio"
ROOTVIEW = ROOT / "app" / "Sources" / "ClaudeStudio" / "Views" / "RootView.swift"
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    try:
        (EVID / "F033").mkdir(parents=True, exist_ok=True)
        png = EVID / "F033-overlay.png"
        # Overlay via ImageRenderer rendern (Fenster-Server-unabhängig).
        subprocess.run([str(APP)], env={**os.environ, "CLAUDESTUDIO_RENDER_OVERLAY": str(png)},
                       capture_output=True, timeout=30)
        assert png.exists() and png.stat().st_size > 5000, "Overlay-PNG nicht gerendert"
        text = subprocess.run(["tesseract", str(png), "stdout", "--psm", "6"],
                              capture_output=True, text=True).stdout.lower()
        assert "tastenk" in text, f"Overlay-Titel fehlt: {text[:160]!r}"
        labels = [l for l in ("session", "befehl", "sidebar", "tab", "schlie") if l in text]
        assert len(labels) >= 4, f"zu wenige Tastenkürzel gelistet: {labels} ({text[:200]!r})"
        assert "cmd" in text and "esc" in text, f"Tasten-Spalte fehlt: {text[:200]!r}"
        # Die ECHTE Cmd+/-Bindung ist in RootView deklariert.
        rv = ROOTVIEW.read_text()
        assert 'keyboardShortcut("/", modifiers: .command)' in rv, "Cmd+/-Bindung fehlt in RootView"
        assert "showShortcuts" in rv, "Overlay-Toggle fehlt in RootView"

        record("F033", "pass", ev("F033", "shortcut-overlay.json",
               {"ocr": text[:300], "labels_found": labels,
                "binding_in_rootview": True, "screen": "test-harness/evidence/F033-overlay.png"}),
               f"Shortcut-Overlay listet die Tastenkürzel ({labels}); Cmd+/-Bindung in RootView deklariert")
    except Exception as e:
        record("F033", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
