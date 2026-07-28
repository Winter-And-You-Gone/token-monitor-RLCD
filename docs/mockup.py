#!/usr/bin/env python3
"""Render a deterministic 400x300 mockup of the current RLCD dashboard."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "mockup.png"
SCALE = 3
W, H = 400, 300

INK = (20, 20, 20)
BG = (242, 243, 238)
LINE = (20, 20, 20)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size * SCALE)
    return ImageFont.load_default()


F_TIME = font(28, True)
F_TITLE = font(20, True)
F_BODY = font(14, True)
F_SMALL = font(12)
F_BAL = font(28, True)


img = Image.new("RGB", (W * SCALE, H * SCALE), BG)
draw = ImageDraw.Draw(img)


def p(v: int | float) -> int:
    return int(round(v * SCALE))


def text(x: int, y: int, value: str, fnt, anchor: str = "la") -> None:
    draw.text((p(x), p(y)), value, font=fnt, fill=INK, anchor=anchor)


def line(x0: int, y0: int, x1: int, y1: int, width: int = 1) -> None:
    draw.line((p(x0), p(y0), p(x1), p(y1)), fill=LINE, width=p(width))


def box(x: int, y: int, w: int, h: int, fill=None, width: int = 1) -> None:
    draw.rectangle((p(x), p(y), p(x + w), p(y + h)), outline=INK, fill=fill, width=p(width))


def pet_icon(cx: int, cy: int) -> None:
    box(cx - 18, cy - 10, 36, 22, fill=BG, width=2)
    draw.ellipse((p(cx - 10), p(cy - 3), p(cx - 5), p(cy + 2)), fill=INK)
    draw.ellipse((p(cx + 5), p(cy - 3), p(cx + 10), p(cy + 2)), fill=INK)
    line(cx - 22, cy + 2, cx - 32, cy + 10, 2)
    line(cx + 22, cy + 2, cx + 32, cy + 10, 2)
    line(cx - 5, cy - 10, cx - 10, cy - 20, 2)
    line(cx + 5, cy - 10, cx + 10, cy - 20, 2)


def weather_icon(cx: int, cy: int) -> None:
    draw.ellipse((p(cx - 16), p(cy - 10), p(cx + 2), p(cy + 8)), outline=INK, width=p(2))
    draw.ellipse((p(cx - 4), p(cy - 17), p(cx + 16), p(cy + 3)), outline=INK, width=p(2))
    draw.rectangle((p(cx - 14), p(cy - 1), p(cx + 18), p(cy + 8)), fill=BG)
    line(cx - 15, cy + 8, cx + 18, cy + 8, 2)


def model_rows(x: int, rows: list[tuple[str, str]]) -> None:
    y = 112
    for name, tok in rows:
        text(x + 12, y, name, F_BODY)
        text(x + 188, y, tok, F_BODY, anchor="ra")
        y += 24


def usage_rows(x: int, rows: list[tuple[str, str, str]]) -> None:
    y = 194
    for label, tok, cost in rows:
        text(x + 12, y, label, F_BODY)
        text(x + 122, y, tok, F_BODY, anchor="ra")
        text(x + 190, y, cost, F_BODY, anchor="ra")
        y += 28


# Header
text(10, 6, "14:30", F_TIME)
text(12, 48, "IN 24.3C  56%RH", F_SMALL)
pet_icon(192, 34)
weather_icon(292, 24)
text(388, 12, "24C", F_TITLE, anchor="ra")
text(388, 48, "SHENZHEN Partly", F_SMALL, anchor="ra")
line(10, 66, 390, 66, 2)
line(10, 268, 390, 268, 2)

# Carousel page 1: Claude + DeepSeek
line(200, 74, 200, 260, 1)
text(50, 78, "CLAUDE", F_TITLE)
model_rows(0, [("opus-4-7", "12.9M"), ("sonnet-4-6", "4.4M"), ("haiku-4-5", "900K")])
line(12, 178, 190, 178, 1)
usage_rows(0, [("today", "382K", "$9.14"), ("month", "8.4M", "$187"), ("total", "18.2M", "$214")])

text(245, 78, "DEEPSEEK", F_TITLE)
text(288, 118, "balance", F_SMALL, anchor="ma")
text(288, 145, "Y70.79", F_BAL, anchor="ma")
line(212, 178, 390, 178, 1)
usage_rows(200, [("grant", "0.00", ""), ("topup", "70.79", ""), ("today", "2.4M", "")])

text(12, 276, "online source ccusage update 14:30", F_SMALL)
text(388, 276, "battery 85%", F_SMALL, anchor="ra")

img = img.resize((W, H), Image.Resampling.LANCZOS)
img.save(OUT)
print(f"saved {OUT}")
