#!/usr/bin/env python3
"""Verifikation Build-Batch 28 (echter Core, echtes tesseract/embed_cli, kein Mock):

  F055  file.attach baut das an Claude gesendete Payload mit absolutem Pfad UND echtem
        Dateiinhalt (Drag&Drop-Substrat).
  F085  projects.scaffold mit FastAPI-Template schreibt eine Projekt-CLAUDE.md mit
        charakteristischem FastAPI-Inhalt auf Disk.
  F178  assets.scan extrahiert Beschreibung + OCR-Text (PNG via tesseract) + SVG-Semantik,
        bettet sie in 'assets' ein; semantische Suche nach 'Firmenlogo' findet das Asset.
"""
from __future__ import annotations
import json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def make_logo_png(path: Path):
    """Rendert klaren schwarzen Text auf Weiß, damit tesseract zuverlässig liest."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (640, 200), "white")
    d = ImageDraw.Draw(img)
    font = None
    for cand in ("/System/Library/Fonts/Supplemental/Arial.ttf",
                 "/System/Library/Fonts/Helvetica.ttc",
                 "/Library/Fonts/Arial.ttf"):
        try:
            font = ImageFont.truetype(cand, 64); break
        except Exception:
            continue
    d.text((30, 60), "ACME CORP LOGO", fill="black", font=font)
    img.save(path)


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b28.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=90)

        # F055 file.attach -> Payload mit absolutem Pfad + echtem Inhalt
        try:
            d = Path(tempfile.mkdtemp(prefix="cs-b28-attach-"))
            src = d / "index.ts"
            content = "export const add = (a: number, b: number) => a + b\n// MARKER_F055\n"
            src.write_text(content)
            r = c.request("file.attach", {"path": str(src)})
            assert r["path"] == str(src) and Path(r["path"]).is_absolute()
            assert r["content"] == content and "MARKER_F055" in r["content"]
            record("F055", "pass", ev("F055", "attach-payload.json",
                   {"path": r["path"], "content_len": len(r["content"]), "has_marker": True}),
                   "Attach-Payload enthält absoluten Pfad UND echten Dateiinhalt")
        except Exception as e:
            record("F055", "fail", note=str(e))

        # F085 FastAPI-Template-Scaffold -> Projekt-CLAUDE.md auf Disk
        try:
            proj = Path(tempfile.mkdtemp(prefix="cs-b28-fastapi-"))
            r = c.request("projects.scaffold", {"path": str(proj), "template": "fastapi"})
            md = (proj / ".claude" / "CLAUDE.md")
            assert md.exists()
            text = md.read_text()
            for marker in ("FastAPI", "uvicorn", "pydantic"):
                assert marker in text, f"fehlt: {marker}"
            record("F085", "pass", ev("F085", "fastapi-claudemd.json",
                   {"path": str(md), "markers": ["FastAPI", "uvicorn", "pydantic"], "content": text}),
                   "Projekt-CLAUDE.md enthält charakteristischen FastAPI-Inhalt (FastAPI/uvicorn/pydantic)")
        except Exception as e:
            record("F085", "fail", note=str(e))

        # F178 assets.scan: OCR (PNG) + SVG-Semantik -> 'assets' -> semantische Suche
        try:
            proj = Path(tempfile.mkdtemp(prefix="cs-b28-assets-"))
            (proj / "logo.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80">'
                '<rect width="200" height="80" fill="#1A73E8"/>'
                '<text x="10" y="50" fill="white">Firmenlogo Acme</text></svg>')
            make_logo_png(proj / "logo.png")
            r = c.request("assets.scan", {"cwd": str(proj), "collection": "assets"})
            by_type = {a["type"]: a for a in r["assets"]}
            assert r["embedded"] >= 2
            # SVG-Semantik enthält den Text 'Firmenlogo'
            assert "Firmenlogo" in by_type["svg"]["svg_text"]
            # OCR lief auf dem PNG und lieferte Text (tesseract)
            ocr = by_type["png"]["ocr_text"]
            assert ocr.strip() != "", "OCR-Text leer"
            ocr_hit = any(w in ocr.upper() for w in ("ACME", "CORP", "LOGO"))
            # semantische Suche nach 'Firmenlogo' findet ein gescanntes Asset
            hits = c.request("knowledge.search",
                             {"query": "Firmenlogo", "collection": "assets", "top_k": 5})["hits"]
            assert hits and "Firmenlogo" in hits[0]["text"] and hits[0]["score"] > 0.5
            record("F178", "pass", ev("F178", "assets-scan.json",
                   {"assets": r["assets"], "ocr_recognised_expected_word": ocr_hit,
                    "top_hit": hits[0]}),
                   f"OCR ('{ocr[:30]}…') + SVG-Semantik in 'assets'; Suche 'Firmenlogo' Score {hits[0]['score']:.3f}")
        except Exception as e:
            record("F178", "fail", note=str(e))

        c.close()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
