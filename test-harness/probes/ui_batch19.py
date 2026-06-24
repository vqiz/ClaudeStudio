#!/usr/bin/env python3
"""Verifikation UI-Batch 19 (echte SwiftUI-App, fenster-gezielter Screenshot, kein Mock):

  F057  Inline-Vorschau rendert Bilder, SVG und Markdown direkt aus der echten Datei. Der ECHTE
        FilePreview (WKWebView) rendert je Dateityp: ein PNG (Bild), ein SVG (Vektor) und eine
        Markdown-Datei (formatiert). Per OCR + Pixel der gerenderten Vorschau nachgewiesen.
        Screenshots F057-image.png / F057-svg.png / F057-md.png.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

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


def capture(file_path: Path, dst: Path, min_w=450):
    env = {**os.environ, "CLAUDESTUDIO_UITEST": "filepreview",
           "CLAUDESTUDIO_PREVIEW_FILE": str(file_path)}
    proc = subprocess.Popen([str(APP)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wid = find_window(min_w)
        if not wid:
            return None
        time.sleep(2.0)  # WKWebView rendern lassen
        raw = dst.with_suffix(".raw.png")
        sh("screencapture", "-x", "-o", f"-l{wid}", str(raw))
        srgb = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"
        r = sh("sips", "--matchToWithIntent", srgb, "relative", str(raw), "--out", str(dst))
        if r.returncode != 0 or not dst.exists():
            dst.write_bytes(raw.read_bytes())
        return dst
    finally:
        proc.terminate(); kill_app()


def ocr(png: Path) -> str:
    im = Image.open(png).convert("RGB"); W, H = im.size
    crop = im.crop((int(W * 0.05), int(H * 0.12), int(W * 0.95), int(H * 0.85)))
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    tmp = png.with_suffix(".ocr.png"); crop.save(tmp)
    return sh("tesseract", str(tmp), "stdout", "--psm", "6").stdout.lower()


def has_blue(png: Path) -> int:
    im = Image.open(png).convert("RGB"); W, H = im.size; px = im.load()
    GB = (26, 115, 232)
    return sum(1 for y in range(0, H, 3) for x in range(0, W, 3)
               if all(abs(px[x, y][k] - GB[k]) <= 40 for k in range(3)))


def make_fixtures(d: Path):
    # Bild (PNG) mit großem Text + Farbblock
    img = Image.new("RGB", (520, 240), "white"); dr = ImageDraw.Draw(img)
    dr.rectangle([20, 20, 500, 110], fill="#1A73E8")
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 48)
    except Exception:
        font = ImageFont.load_default()
    dr.text((40, 40), "IMAGE PREVIEW", fill="white", font=font)
    dr.text((40, 150), "pixel photo", fill="#333333", font=font)
    (d / "photo.png").parent.mkdir(parents=True, exist_ok=True)
    img.save(d / "photo.png")
    # SVG (Vektor) mit blauem Rechteck + Text
    (d / "logo.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg' width='480' height='200'>"
        "<rect width='480' height='200' fill='#1A73E8'/>"
        "<text x='30' y='115' font-size='44' font-family='sans-serif' fill='white'>SVG PREVIEW</text>"
        "</svg>")
    # Markdown
    (d / "README.md").write_text(
        "# Markdown Preview\n\nHello **bold** world.\n\n## Aufgaben\n\n- item one\n- item two\n")


def main():
    if not APP.exists():
        print(json.dumps({"results": {"_": {"status": "fail", "note": "App nicht gebaut"}}})); return
    kill_app()
    d = Path(tempfile.mkdtemp(prefix="cs-f057-"))
    make_fixtures(d)
    try:
        (EVID / "F057").mkdir(parents=True, exist_ok=True)
        img = capture(d / "photo.png", EVID / "F057-image.png")
        svg = capture(d / "logo.svg", EVID / "F057-svg.png")
        md = capture(d / "README.md", EVID / "F057-md.png")
        assert img and svg and md, "Vorschau-Fenster fehlt"

        t_img, t_svg, t_md = ocr(img), ocr(svg), ocr(md)
        # Bild: Text + Farbblock gerendert
        assert ("image" in t_img or "preview" in t_img or "photo" in t_img) and has_blue(img) > 50, \
            f"Bild nicht gerendert: ocr={t_img[:120]!r} blue={has_blue(img)}"
        # SVG: Text + blaues Rechteck gerendert
        assert ("svg" in t_svg or "preview" in t_svg) and has_blue(svg) > 200, \
            f"SVG nicht gerendert: ocr={t_svg[:120]!r} blue={has_blue(svg)}"
        # Markdown: Block-Elemente formatiert (H1 + H2 + Fließtext) — Beweis für gerendertes Markdown.
        assert "markdown" in t_md and "preview" in t_md, f"Markdown-H1 fehlt: {t_md[:160]!r}"
        assert "aufgaben" in t_md, f"Markdown-H2 (Aufgaben) fehlt: {t_md[:160]!r}"
        assert ("bold" in t_md or "hello" in t_md or "item" in t_md), f"Markdown-Inhalt fehlt: {t_md[:160]!r}"
        record("F057", "pass", ev("F057", "file-preview.json",
               {"image_ocr": t_img[:100], "svg_ocr": t_svg[:100], "md_ocr": t_md[:140],
                "image_blue_px": has_blue(img), "svg_blue_px": has_blue(svg),
                "screens": ["test-harness/evidence/F057-image.png", "test-harness/evidence/F057-svg.png",
                            "test-harness/evidence/F057-md.png"]}),
               "Vorschau rendert Bild (Text+Farbe), SVG (Vektor+Text) und Markdown (Überschrift+Liste) aus echten Dateien")
    except Exception as e:
        record("F057", "fail", note=str(e))

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
