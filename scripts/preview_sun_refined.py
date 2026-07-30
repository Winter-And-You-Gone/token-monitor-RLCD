#!/usr/bin/env python3
"""Preview a refined sun based ON the current real sun (cluster-extracted).

Keeps the existing topology exactly: octagonal core + 4 cardinal rectangular
rays + 4 diagonal diamond rays, same shapes. The only refinement: close the
2px gap between each ray and the core so rays read as emanating from the body
instead of floating beside it. No new design - just tightening the current one.

Outputs:
  docs/preview/sun_refined_static.png      - refined (rays touching core)
  docs/preview/sun_real_repro.png          - current (unchanged, for compare)
  docs/preview/sun_compare_orig_vs_refined.png
"""
from pathlib import Path
import json
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "preview"
SIZE = 48
S = 10
CLUSTERS = OUT / "sun_clusters.json"

data = json.loads(CLUSTERS.read_text())
core = [tuple(p) for p in data["core"]["pixels"]]
core_cx, core_cy = data["core"]["centroid"]
rays = data["rays"]


def stamp(grid, pts, val=255):
    for x, y in pts:
        ix, iy = int(round(x)), int(round(y))
        if 0 <= ix < SIZE and 0 <= iy < SIZE:
            grid[iy][ix] = val


def render(ray_shift):
    """ray_shift: per-ray (dx,dy) to apply, in addition to original position."""
    grid = [[0] * SIZE for _ in range(SIZE)]
    stamp(grid, core)
    for i, r in enumerate(rays):
        cxr, cyr = r["centroid"]
        dx, dy = ray_shift[i]
        for px, py in r["pixels_rel"]:
            stamp(grid, [(cxr + px + dx, cyr + py + dy)])
    return grid


def grid_to_image(grid):
    img = Image.new("L", (SIZE, SIZE), 0)
    px = img.load()
    for y in range(SIZE):
        for x in range(SIZE):
            px[x, y] = grid[y][x]
    return img.resize((SIZE * S, SIZE * S), Image.NEAREST)


# Current positions have a ~2px gap between ray root and core edge. Shift each
# ray toward the core along its own axis to close the gap.
# Cardinal rays: top ray centroid y=5, core top edge y=14 -> gap ~3 (root at y~8).
#   Shift top ray +2 in y (down, toward core). Similarly bottom up, left right, right left.
# Diagonal rays: centroid at (8,8) etc, core corner ~ (15,15) -> shift toward center.
shifts = {
    0: (0, 2),    # top cardinal: move down toward core
    1: (2, 0),    # left cardinal: move right toward core
    2: (-2, 0),   # right cardinal: move left toward core
    3: (0, -2),   # bottom cardinal: move up toward core
    4: (2, 2),    # diag top-left: move toward center
    5: (-2, 2),   # diag top-right
    6: (2, -2),   # diag bottom-left
    7: (-2, -2),  # diag bottom-right
}
ray_shift = [shifts[i] for i in range(8)]

refined = render(ray_shift)

# Also render the unchanged current sun for comparison.
current = render([(0, 0)] * 8)

OUT.mkdir(parents=True, exist_ok=True)
grid_to_image(current).save(OUT / "sun_real_repro.png")
grid_to_image(refined).save(OUT / "sun_refined_static.png")

# side-by-side
c = grid_to_image(current).resize((SIZE * S, SIZE * S))
n = grid_to_image(refined).resize((SIZE * S, SIZE * S))
sheet = Image.new("L", (c.width + 20 + n.width, c.height), 255)
sheet.paste(c, (0, 0))
sheet.paste(n, (c.width + 20, 0))
sheet.save(OUT / "sun_compare_orig_vs_refined.png")
print("wrote current, refined, and compare")
