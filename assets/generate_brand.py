#!/usr/bin/env python3
"""
ClaudeStudio brand asset generator.

Renders the ClaudeStudio logo, macOS app icon (.icns + .appiconset), and the
README banner from code so the branding is fully reproducible. Pure Pillow — no
SVG rasterizer required.

    python3 assets/generate_brand.py

Outputs (relative to repo root):
    assets/logo.png                 1024px squircle mark
    assets/logo-mark.png            1024px transparent glyph (no tile)
    assets/banner.png               1280x640 README hero
    app/Resources/AppIcon.icns      packaged macOS icon
    app/Resources/Assets.xcassets/AppIcon.appiconset/   Xcode icon set
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = Path(__file__).resolve().parent.parent
ASSETS = REPO / "assets"
RESOURCES = REPO / "app" / "Resources"

# ---- Brand palette -------------------------------------------------------
INDIGO = (99, 102, 241)     # #6366F1  gradient start
VIOLET = (139, 63, 246)     # #8B3FF6  gradient end
CORAL = (251, 113, 133)     # #FB7185  active-agent accent
WHITE = (255, 255, 255)
INK = (14, 14, 20)          # #0E0E14  banner background
SUBTLE = (158, 160, 180)    # banner secondary text

SS = 4  # supersampling factor for anti-aliasing


def _font(paths, size, variation=None):
    for p in paths:
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, size)
                if variation:
                    try:
                        f.set_variation_by_name(variation)
                    except Exception:
                        pass
                return f
            except Exception:
                continue
    return ImageFont.load_default()


SF = ["/System/Library/Fonts/SFNS.ttf"]
SF_ROUND = ["/System/Library/Fonts/SFNSRounded.ttf"]
SF_MONO = ["/System/Library/Fonts/SFNSMono.ttf", "/System/Library/Fonts/Menlo.ttc"]
ARIAL_B = ["/System/Library/Fonts/Supplemental/Arial Bold.ttf"]
ARIAL = ["/System/Library/Fonts/Supplemental/Arial.ttf"]


def squircle_mask(size: int, n: float = 5.0, pad: int = 0) -> Image.Image:
    """A superellipse (iOS/macOS 'squircle') alpha mask, 255 inside."""
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    cx = cy = size / 2.0
    r = size / 2.0 - pad
    steps = 1440
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        ct, st = math.cos(t), math.sin(t)
        x = cx + r * math.copysign(abs(ct) ** (2.0 / n), ct)
        y = cy + r * math.copysign(abs(st) ** (2.0 / n), st)
        pts.append((x, y))
    d.polygon(pts, fill=255)
    return m


def diagonal_gradient(size: int, c0, c1) -> Image.Image:
    small = Image.new("RGB", (256, 256))
    px = small.load()
    for y in range(256):
        for x in range(256):
            t = (x + y) / (2 * 255.0)
            px[x, y] = tuple(int(c0[k] + (c1[k] - c0[k]) * t) for k in range(3))
    return small.resize((size, size), Image.BILINEAR)


def draw_mark(size: int, glyph_only: bool = False) -> Image.Image:
    """Render the ClaudeStudio mark at `size` px (RGBA)."""
    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    if not glyph_only:
        grad = diagonal_gradient(S, INDIGO, VIOLET).convert("RGBA")
        mask = squircle_mask(S, n=5.0, pad=0)
        img.paste(grad, (0, 0), mask)

        # Soft top-left sheen for depth.
        sheen = Image.new("L", (S, S), 0)
        sd = ImageDraw.Draw(sheen)
        sd.ellipse([-S * 0.2, -S * 0.35, S * 0.95, S * 0.55], fill=70)
        sheen = sheen.filter(ImageFilter.GaussianBlur(S * 0.06))
        white_layer = Image.new("RGBA", (S, S), (255, 255, 255, 255))
        sheen_masked = Image.composite(white_layer, Image.new("RGBA", (S, S), (0, 0, 0, 0)),
                                       Image.composite(sheen, Image.new("L", (S, S), 0), mask))
        img = Image.alpha_composite(img, sheen_masked)

    d = ImageDraw.Draw(img)
    cx = cy = S / 2.0

    # Orchestration graph: a central supervisor node + three agent satellites.
    orbit = S * 0.275
    sat_r = S * 0.072
    core_r = S * 0.125
    angles = [-90, 150, 30]  # top, lower-left, lower-right (triangle)
    sats = [(cx + orbit * math.cos(math.radians(a)),
             cy + orbit * math.sin(math.radians(a))) for a in angles]

    edge_w = max(2, int(S * 0.020))
    edge_col = (255, 255, 255, 150) if not glyph_only else (255, 255, 255, 90)
    for (sx, sy) in sats:
        d.line([(cx, cy), (sx, sy)], fill=edge_col, width=edge_w)

    glyph_fill = WHITE + (255,) if not glyph_only else INDIGO + (255,)
    # Satellites (top one is the "active" coral agent).
    for i, (sx, sy) in enumerate(sats):
        col = CORAL + (255,) if i == 0 else glyph_fill
        d.ellipse([sx - sat_r, sy - sat_r, sx + sat_r, sy + sat_r], fill=col)

    # Central core with a ring cut-out for a "kernel" feel.
    d.ellipse([cx - core_r, cy - core_r, cx + core_r, cy + core_r], fill=glyph_fill)
    inner = core_r * 0.42
    hole = INDIGO + (255,) if not glyph_only else (0, 0, 0, 0)
    if not glyph_only:
        # Punch a subtle gradient-coloured hole so the core reads as a ring/aperture.
        d.ellipse([cx - inner, cy - inner, cx + inner, cy + inner], fill=(124, 80, 240, 255))

    return img.resize((size, size), Image.LANCZOS)


def build_icon_master() -> Image.Image:
    return draw_mark(1024, glyph_only=False)


def write_iconset(master: Image.Image):
    iconset = ASSETS / "AppIcon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True, exist_ok=True)
    specs = [
        ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
    ]
    for name, px in specs:
        master.resize((px, px), Image.LANCZOS).save(iconset / name)

    RESOURCES.mkdir(parents=True, exist_ok=True)
    icns = RESOURCES / "AppIcon.icns"
    try:
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], check=True)
        print(f"  wrote {icns.relative_to(REPO)}")
    except Exception as e:
        print(f"  iconutil failed ({e}); .icns not generated")

    # Xcode asset catalog (for a future .xcodeproj packaging).
    appiconset = RESOURCES / "Assets.xcassets" / "AppIcon.appiconset"
    if appiconset.exists():
        shutil.rmtree(appiconset)
    appiconset.mkdir(parents=True, exist_ok=True)
    images = []
    for scale in ("1x", "2x"):
        for base in (16, 32, 128, 256, 512):
            px = base * (2 if scale == "2x" else 1)
            fn = f"icon_{base}x{base}{'@2x' if scale == '2x' else ''}.png"
            master.resize((px, px), Image.LANCZOS).save(appiconset / fn)
            images.append({"size": f"{base}x{base}", "idiom": "mac",
                           "filename": fn, "scale": scale})
    (appiconset / "Contents.json").write_text(json.dumps(
        {"images": images, "info": {"version": 1, "author": "ClaudeStudio"}}, indent=2))
    (RESOURCES / "Assets.xcassets" / "Contents.json").write_text(json.dumps(
        {"info": {"version": 1, "author": "ClaudeStudio"}}, indent=2))
    shutil.rmtree(iconset)
    print(f"  wrote {appiconset.relative_to(REPO)} (10 images)")


def write_banner(mark: Image.Image):
    W, H = 1280, 640
    top, bot = (18, 18, 26), (10, 10, 15)
    grad = Image.new("RGB", (1, H))
    for y in range(H):
        t = y / H
        grad.putpixel((0, y), tuple(int(top[k] + (bot[k] - top[k]) * t) for k in range(3)))
    img = grad.resize((W, H))

    # Soft indigo glow behind the mark.
    glow = Image.new("RGB", (W, H), INK)
    gd = ImageDraw.Draw(glow)
    gd.ellipse([120, 120, 620, 620], fill=(60, 50, 150))
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    img = Image.blend(img, glow, 0.55)

    # Faint dotted grid for a "developer canvas" texture.
    dot = ImageDraw.Draw(img)
    for gy in range(40, H, 40):
        for gx in range(40, W, 40):
            dot.ellipse([gx, gy, gx + 1, gy + 1], fill=(38, 40, 56))

    # Mark on the left.
    m = mark.resize((300, 300), Image.LANCZOS)
    img.paste(m, (96, H // 2 - 150), m)

    d = ImageDraw.Draw(img)
    x = 452
    title_font = _font(SF, 116, "Bold") if os.path.exists(SF[0]) else _font(ARIAL_B, 110)
    tag_font = _font(SF, 38, "Regular") if os.path.exists(SF[0]) else _font(ARIAL, 36)
    chip_font = _font(SF_MONO, 28)

    d.text((x, 214), "ClaudeStudio", font=title_font, fill=WHITE)
    d.text((x + 4, 348), "A native macOS GUI & Agentic OS for Claude Code",
           font=tag_font, fill=SUBTLE)

    chips = ["Swift", "Rust", "Agentic OS", "MIT"]
    cx = x + 4
    for c in chips:
        bb = d.textbbox((0, 0), c, font=chip_font)
        w = bb[2] - bb[0]
        d.rounded_rectangle([cx, 408, cx + w + 36, 460], radius=12,
                            outline=(90, 92, 120), width=2)
        d.text((cx + 18, 416), c, font=chip_font, fill=(196, 198, 220))
        cx += w + 36 + 16

    ASSETS.mkdir(parents=True, exist_ok=True)
    img.save(ASSETS / "banner.png")
    print(f"  wrote {(ASSETS / 'banner.png').relative_to(REPO)}")


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    print("Generating ClaudeStudio brand assets…")
    master = build_icon_master()
    master.save(ASSETS / "logo.png")
    print(f"  wrote {(ASSETS / 'logo.png').relative_to(REPO)}")

    glyph = draw_mark(1024, glyph_only=True)
    glyph.save(ASSETS / "logo-mark.png")
    print(f"  wrote {(ASSETS / 'logo-mark.png').relative_to(REPO)}")

    write_iconset(master)
    write_banner(master)
    print("Done.")


if __name__ == "__main__":
    main()
