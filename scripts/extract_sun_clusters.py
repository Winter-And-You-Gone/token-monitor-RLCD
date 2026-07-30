#!/usr/bin/env python3
"""Extract core + 8 ray pixel-clusters from the ACTUAL icon_sun in icons.c.
Save each ray cluster as an offset list (relative to its own centroid) so we
can reproduce the EXACT ray shapes when animating (translate/rotate the real
pixels instead of redrawing approximations)."""
from pathlib import Path
import re, math, json
from collections import deque
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
src = (ROOT / "firmware" / "components" / "ui_app" / "icons.c").read_text(encoding="utf-8", errors="replace")
m = re.search(r"static const uint8_t sun_map\[\]\s*=\s*\{([^}]*)\};", src)
nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
SIZE = 48
def on(x, y): return nums[y * SIZE + x] > 127

# BFS connected components
seen = set()
comps = []
for y in range(SIZE):
    for x in range(SIZE):
        if on(x, y) and (x, y) not in seen:
            q = deque([(x, y)]); comp = []
            while q:
                cx, cy = q.popleft()
                if (cx, cy) in seen or not (0 <= cx < SIZE and 0 <= cy < SIZE) or not on(cx, cy):
                    continue
                seen.add((cx, cy)); comp.append((cx, cy))
                for nx, ny in ((cx+1,cy),(cx-1,cy),(cx,cy+1),(cx,cy-1),(cx+1,cy+1),(cx-1,cy-1),(cx+1,cy-1),(cx-1,cy+1)):
                    q.append((nx, ny))
            comps.append(comp)

# largest component = core; rest = rays
comps.sort(key=len, reverse=True)
core = comps[0]
rays = comps[1:]
core_cx = sum(p[0] for p in core)/len(core)
core_cy = sum(p[1] for p in core)/len(core)

out = {
    "size": SIZE,
    "core": {"centroid": [core_cx, core_cy], "pixels": core},
    "rays": []
}
for r in rays:
    cxr = sum(p[0] for p in r)/len(r)
    cyr = sum(p[1] for p in r)/len(r)
    ang = math.degrees(math.atan2(cyr-core_cy, cxr-core_cx)) % 360
    # relative pixels to ray centroid
    rel = [[p[0]-cxr, p[1]-cyr] for p in r]
    out["rays"].append({"centroid": [cxr, cyr], "angle": ang, "pixels_rel": rel})

(ROOT / "docs" / "preview").mkdir(parents=True, exist_ok=True)
with open(ROOT / "docs" / "preview" / "sun_clusters.json", "w") as f:
    json.dump(out, f)

print(f"core: {len(core)} px at ({core_cx:.1f},{core_cy:.1f})")
print(f"rays: {len(rays)} clusters")
for i, r in enumerate(out["rays"]):
    print(f"  ray{i}: angle={r['angle']:.0f}° pts={len(r['pixels_rel'])} centroid=({r['centroid'][0]:.1f},{r['centroid'][1]:.1f})")
