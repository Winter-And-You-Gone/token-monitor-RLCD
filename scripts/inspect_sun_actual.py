#!/usr/bin/env python3
"""Precisely measure the ACTUAL icon_sun geometry from icons.c sun_map[].
Output: core radius, each ray's bounding box, centroid, shape classification
(rect vs diamond), gap from core. So we can reproduce it exactly."""
from pathlib import Path
import re
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
src = (ROOT / "firmware" / "components" / "ui_app" / "icons.c").read_text(encoding="utf-8", errors="replace")
m = re.search(r"static const uint8_t sun_map\[\]\s*=\s*\{([^}]*)\};", src)
nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
SIZE = 48
im = Image.new("L", (SIZE, SIZE))
im.putdata(nums)

def on(x, y): return nums[y * SIZE + x] > 127

# core: largest connected blob near center. Find core radius = max dist from center
# among ON pixels that are within the central blob (connected to center).
cx = cy = SIZE // 2
# BFS from center over ON pixels
from collections import deque
seen = set()
q = deque([(cx, cy)])
core_pts = []
if not on(cx, cy):
    # find nearest ON pixel to center
    best = None; bd = 1e9
    for y in range(SIZE):
        for x in range(SIZE):
            if on(x, y):
                dd = (x-cx)**2 + (y-cy)**2
                if dd < bd: bd = dd; best = (x, y)
    q = deque([best]); cx0, cy0 = best
else:
    cx0, cy0 = cx, cy
while q:
    x, y = q.popleft()
    if (x, y) in seen: continue
    if not (0 <= x < SIZE and 0 <= y < SIZE): continue
    if not on(x, y): continue
    seen.add((x, y))
    core_pts.append((x, y))
    for nx, ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1),(x+1,y+1),(x-1,y-1),(x+1,y-1),(x-1,y+1)):
        q.append((nx, ny))

# core radius = max dist from (cx0,cy0) among core_pts
import math
core_r = max(math.hypot(x-cx0, y-cy0) for x, y in core_pts) if core_pts else 0
print(f"core centroid=({cx0},{cy0})  core_radius≈{core_r:.1f}px  core_pixels={len(core_pts)}")

# rays = all ON pixels NOT in core blob. Cluster them by angle from center.
ray_pts = [(x, y) for y in range(SIZE) for x in range(SIZE) if on(x, y) and (x, y) not in seen]
# cluster by angle (45deg bins)
bins = {}
for x, y in ray_pts:
    ang = math.degrees(math.atan2(y-cy0, x-cx0)) % 360
    b = int(round(ang / 45) % 8)
    bins.setdefault(b, []).append((x, y))
print(f"\n{len(bins)} ray clusters:")
for b in sorted(bins):
    pts = bins[b]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    # centroid
    cxb = sum(xs)/len(xs); cyb = sum(ys)/len(pts)
    w = max(xs)-min(xs)+1; h = max(ys)-min(ys)+1
    # shape: if width≈height -> diamond/square; if one dim >> other -> rect
    shape = "rect" if abs(w-h) > 2 else "square/diamond"
    # min distance from core edge to ray
    mind = min(math.hypot(x-cx0,y-cy0) for x,y in pts) - core_r
    maxd = max(math.hypot(x-cx0,y-cy0) for x,y in pts) - core_r
    ang_c = math.degrees(math.atan2(cyb-cy0, cxb-cx0)) % 360
    print(f"  bin{b} angle~{ang_c:.0f}°  pts={len(pts)}  bbox={w}x{h}  gap={mind:.1f}  raylen={maxd:.1f}  shape={shape}")

# save labeled
big = im.resize((SIZE*8, SIZE*8), Image.NEAREST)
big.save(ROOT/"docs"/"preview"/"sun_ACTUAL_from_icons_c.png")
