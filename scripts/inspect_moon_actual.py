#!/usr/bin/env python3
"""Extract the ACTUAL icon_moon pixels from icons.c and render as PNG,
plus analyze its shape (full moon? crescent? cratered?) for designing phases."""
from pathlib import Path
import re
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "preview"
OUT.mkdir(parents=True, exist_ok=True)

src = (ROOT / "firmware" / "components" / "ui_app" / "icons.c").read_text(encoding="utf-8", errors="replace")
m = re.search(r"static const uint8_t moon_map\[\]\s*=\s*\{([^}]*)\};", src)
nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
SIZE = 48
im = Image.new("L", (SIZE, SIZE))
im.putdata(nums)
big = im.resize((SIZE * 10, SIZE * 10), Image.NEAREST)
big.save(OUT / "moon_ACTUAL.png")

# analyze: bounding box, is it a full disk or crescent?
on = [(x, y) for y in range(SIZE) for x in range(SIZE) if nums[y * SIZE + x] > 0]
xs = [p[0] for p in on]; ys = [p[1] for p in on]
print(f"moon ON={len(on)}, bbox x[{min(xs)}..{max(xs)}] y[{min(ys)}..{max(ys)}]")
# column fill profile: how many ON pixels per column (full disk = tall middle, crescent = skewed)
cx_mid = (min(xs) + max(xs)) / 2
left_on = sum(1 for x, y in on if x < cx_mid)
right_on = sum(1 for x, y in on if x >= cx_mid)
print(f"left half ON={left_on}, right half ON={right_on} (full disk ~equal, crescent skewed)")
# row profile to detect craters (gaps)
print("row ON counts (every 4th row):", [sum(1 for x,y in on if y==r) for r in range(0, SIZE, 4)])
