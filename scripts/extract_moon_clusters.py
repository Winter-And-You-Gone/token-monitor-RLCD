#!/usr/bin/env python3
"""Extract the existing icon_moon LIT pixels from icons.c, save as a cluster
JSON for the moon-phase generator. The existing icon is a right-opening
crescent (lit on left), positioned at col4-27. We'll use it as frame0 and
generate the rest of the phases by carving the disk."""
from pathlib import Path
import re, json
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "preview"
OUT.mkdir(parents=True, exist_ok=True)

src = (ROOT / "firmware" / "components" / "ui_app" / "icons.c").read_text(encoding="utf-8", errors="replace")
m = re.search(r"static const uint8_t moon_map\[\]\s*=\s*\{([^}]*)\};", src)
nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
SIZE = 48

# lit pixels of existing moon
lit = [(x, y) for y in range(SIZE) for x in range(SIZE) if nums[y * SIZE + x] > 0]

# Save existing moon as PNG (already done in inspect script, redo here)
im = Image.new("L", (SIZE, SIZE))
im.putdata(nums)
im.resize((SIZE * 10, SIZE * 10), Image.NEAREST).save(OUT / "moon_existing_actual.png")

# Find the disk this crescent belongs to. The crescent implies a full disk.
# Bbox of lit pixels:
xs = [p[0] for p in lit]; ys = [p[1] for p in lit]
bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)
print(f"existing moon lit bbox: x[{bx0}..{bx1}] y[{by0}..{by1}], count={len(lit)}")

# The full disk center/radius: bbox center. The crescent is the LEFT part of
# a disk whose right side is carved by an ellipse. Estimate disk R from height.
disk_cx = (bx0 + bx1) / 2
disk_cy = (by0 + by1) / 2
disk_r = (by1 - by0) / 2
print(f"estimated disk: cx={disk_cx}, cy={disk_cy}, r={disk_r}")

# Save cluster json
data = {
    "size": SIZE,
    "existing_lit": lit,
    "disk_cx": disk_cx,
    "disk_cy": disk_cy,
    "disk_r": disk_r,
    "bbox": [bx0, by0, bx1, by1],
}
(OUT / "moon_clusters.json").write_text(json.dumps(data))
print("wrote moon_clusters.json")
