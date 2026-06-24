#!/usr/bin/env python3
"""Verifikation UI-Batch 18 (echte SwiftUI-App, echter WKWebView, echter localhost-Server, kein Mock):

  F359  Browser-Preview: die App bettet eine localhost-Dev-Server-Vorschau über einen ECHTEN WKWebView
        direkt ein und aktualisiert sie bei Änderungen. Nachweis: der WKWebView lädt die lokale Seite
        (Inhalt ALPHA); nach Änderung der Datei am Server erscheint per Live-Reload der neue Inhalt
        (BRAVO) — im SELBEN laufenden App-Fenster. Per OCR beider Screenshots nachgewiesen.
        Screenshots F359-before.png / F359-after.png.
"""
from __future__ import annotations
import json, os, subprocess, sys, threading, time
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402
from PIL import Image  # noqa: E402

ROOT = P.ROOT
EVID = ROOT / "test-harness" / "evidence"
APP = ROOT / "app" / ".build" / "debug" / "ClaudeStudio"
WINDOWID = ROOT / "test-harness" / "lib" / "windowid"
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True)


def kill_app():
    sh("pkill", "-f", "ClaudeStudio"); time.sleep(0.8)


def find_window(min_w):
    for _ in range(30):
        time.sleep(0.4)
        out = sh(str(WINDOWID), "ClaudeStudio").stdout
        best = None
        for line in out.splitlines():
            p = line.split("\t")
            if len(p) >= 4 and p[3] == "0" and int(p[1]) >= min_w:
                if best is None or int(p[1]) > best[1]:
                    best = (p[0], int(p[1]))
        if best:
            return best[0]
    return None


def shoot(wid, dst: Path):
    raw = dst.with_suffix(".raw.png")
    sh("screencapture", "-x", "-o", f"-l{wid}", str(raw))
    srgb = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"
    r = sh("sips", "--matchToWithIntent", srgb, "relative", str(raw), "--out", str(dst))
    if r.returncode != 0 or not dst.exists():
        dst.write_bytes(raw.read_bytes())
    return dst


def ocr(png: Path) -> str:
    im = Image.open(png).convert("RGB"); W, H = im.size
    # WebView-Inhaltsbereich (unter der Kopfleiste).
    crop = im.crop((int(W * 0.06), int(H * 0.12), int(W * 0.94), int(H * 0.80)))
    tmp = png.with_suffix(".ocr.png"); crop.save(tmp)
    return sh("tesseract", str(tmp), "stdout", "--psm", "6").stdout.lower()


def page(tag: str) -> str:
    return ("<!doctype html><html><head><meta charset='utf-8'></head>"
            "<body style='font-family:sans-serif;margin:60px'>"
            f"<h1 style='font-size:80px'>PREVIEW {tag}</h1>"
            f"<p style='font-size:40px'>marker {tag}</p></body></html>")


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    docroot = Path(P.ROOT) / "test-harness" / "evidence" / "F359"
    docroot.mkdir(parents=True, exist_ok=True)
    index = docroot / "index.html"
    index.write_text(page("ALPHA"))

    handler = partial(SimpleHTTPRequestHandler, directory=str(docroot))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"

    proc = None
    try:
        env = {**os.environ, "CLAUDESTUDIO_UITEST": "webpreview", "CLAUDESTUDIO_PREVIEW_URL": url}
        proc = subprocess.Popen([str(APP)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wid = find_window(600)
        assert wid, "Preview-Fenster nicht gefunden"
        time.sleep(3.0)  # WebView lädt + rendert
        before = shoot(wid, EVID / "F359-before.png")
        t_before = ocr(before)

        # Datei am "Dev-Server" ändern -> Live-Reload soll den neuen Inhalt zeigen
        index.write_text(page("BRAVO"))
        time.sleep(3.5)  # Reload-Timer (1s) + Cache-Bypass
        after = shoot(wid, EVID / "F359-after.png")
        t_after = ocr(after)

        assert "alpha" in t_before, f"eingebettete Vorschau zeigt ALPHA nicht: {t_before[:160]!r}"
        assert "bravo" not in t_before, f"BRAVO schon vor der Änderung sichtbar: {t_before[:160]!r}"
        assert "bravo" in t_after, f"Live-Reload zeigt BRAVO nicht: {t_after[:160]!r}"
        record("F359", "pass", ev("F359", "browser-preview.json",
               {"url": url, "before_ocr": t_before[:160], "after_ocr": t_after[:160],
                "screens": ["test-harness/evidence/F359-before.png", "test-harness/evidence/F359-after.png"]}),
               "Eingebetteter WKWebView zeigt localhost-Seite (ALPHA); nach Datei-Änderung Live-Reload auf BRAVO")
    except Exception as e:
        record("F359", "fail", note=str(e))
    finally:
        if proc:
            proc.terminate()
        server.shutdown(); kill_app()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
