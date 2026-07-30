#!/usr/bin/env python3
"""Preview a fact-oriented west-to-east Earth rotation.

The existing icon is a black globe with white hand-drawn land cutouts. The
black circular limb/ocean stays fixed; only the white land texture moves. A
positive phase advances surface features from screen-left to screen-right,
which is the requested west-to-east direction in the front view.
"""
from pathlib import Path
import re, math
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "preview"
OUT.mkdir(parents=True, exist_ok=True)
SIZE = 48
S = 8
FRAMES = 12
CX = CY = 24.0
R = 20.0

src = (ROOT / "firmware" / "components" / "ui_app" / "icons.c").read_text(encoding="utf-8", errors="replace")
nums = [int(x) for x in re.findall(r"\d+", re.search(r"earth_map\[\]\s*=\s*\{([^}]*)\}", src).group(1))]
source = {(x, y) for y in range(SIZE) for x in range(SIZE) if nums[y * SIZE + x] > 0}
disk = {(x, y) for y in range(SIZE) for x in range(SIZE)
        if (x - CX) ** 2 + (y - CY) ** 2 <= R * R}


def source_ink_at(x, y):
    if 0 <= x < SIZE and 0 <= y < SIZE:
        return nums[y * SIZE + x] > 0
    return True  # ocean/black when sampling outside the source disk


def render(phase):
    # Base: fixed black globe. White source pixels inside the globe are land.
    if phase == 0:
        return set(source)
    out = set(disk)
    for y in range(SIZE):
        for x in range(SIZE):
            if (x, y) not in disk:
                continue
            nx = max(-0.999, min(0.999, (x - CX) / R))
            lon_out = math.asin(nx)
            # Positive phase moves each source feature to the right/east.
            lon_src = lon_out - phase
            # Wrap source longitude around the globe.
            lon_src = (lon_src + math.pi) % (2 * math.pi) - math.pi
            x_src = CX + R * math.sin(lon_src)
            # Keep latitude fixed; round sampling preserves chunky hand-drawn art.
            y_src = int(round(y))
            x_src_i = int(round(x_src))
            if not source_ink_at(x_src_i, y_src):
                out.discard((x, y))  # white land cutout
    return out


frames = [render(2 * math.pi * i / FRAMES) for i in range(FRAMES)]
for i, pixels in enumerate(frames):
    im = Image.new("L", (SIZE, SIZE), 0)
    d = ImageDraw.Draw(im)
    for x, y in pixels:
        d.point((x, y), fill=255)
    # Match the screen: black ink on white background.
    screen = Image.eval(im, lambda v: 255 - v)
    screen.resize((SIZE * S, SIZE * S), Image.NEAREST).save(OUT / f"earth_spin_f{i}.png")

sheet = Image.new("L", (SIZE * S * FRAMES, SIZE * S), 255)
for i in range(FRAMES):
    sheet.paste(Image.open(OUT / f"earth_spin_f{i}.png"), (i * SIZE * S, 0))
sheet.save(OUT / "earth_spin_contact.png")

print("per-frame black ON:", [len(f) for f in frames])
print("frame0 == existing:", frames[0] == source)
print("phase step:", 360 / FRAMES, "degrees; positive phase = screen-left -> screen-right")
print("wrote earth_spin_contact.png")
