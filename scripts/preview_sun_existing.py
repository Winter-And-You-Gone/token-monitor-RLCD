#!/usr/bin/env python3
"""Render the EXISTING icon_sun from icons.c sun_map[] as a PNG preview,
by re-running the same gen_icons.py weather_alpha('clear') routine that
produced it, so we can compare against my hand-drawn preview."""
from pathlib import Path
import sys, math
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "preview"
OUT.mkdir(parents=True, exist_ok=True)

SIZE = 48
S = 8  # upscale for visibility

def existing_clear(size, S):
    """Reproduce gen_icons.weather_alpha('clear') exactly."""
    im = Image.new("L", (size * S, size * S), 0)
    d = ImageDraw.Draw(im)
    cx = cy = size * S / 2
    W = 2 * S
    def E(x0, y0, x1, y1, w=W, f=None): d.ellipse([x0, y0, x1, y1], outline=255, width=w, fill=f)
    def L(x0, y0, x1, y1, w=W): d.line([x0, y0, x1, y1], fill=255, width=w)
    su = size * S * 0.30
    E(cx - su, cy - su, cx + su, cy + su)
    for a in range(0, 360, 45):
        dx, dy = math.cos(math.radians(a)), math.sin(math.radians(a))
        L(cx + dx * (su + 3 * S), cy + dy * (su + 3 * S),
          cx + dx * (su + 8 * S), cy + dy * (su + 8 * S))
    return im.resize((size, size), Image.LANCZOS).resize((size * S, size * S), Image.NEAREST)

existing_clear(SIZE, S).save(OUT / "sun_EXISTING_actual.png")
print("wrote sun_EXISTING_actual.png")
