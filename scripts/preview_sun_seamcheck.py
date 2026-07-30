#!/usr/bin/env python3
"""Quantify seam + blur: compare frame[0] vs frame[12-computed] and measure
ray sharpness per frame. No vision MCP needed."""
from pathlib import Path
import json, math

ROOT = Path(__file__).resolve().parents[1]
SIZE = 48
FRAMES = 12
CLUSTERS = ROOT / "docs" / "preview" / "sun_clusters.json"
data = json.loads(CLUSTERS.read_text())
core = data["core"]["pixels"]
rays = data["rays"]
core_cx, core_cy = data["core"]["centroid"]


def render(bts_grid):
    return bytes(bts_grid)


def make_frame(t, off):
    grid = [[0] * SIZE for _ in range(SIZE)]
    for x, y in core:
        grid[int(y)][int(x)] = 255
    breath = 0.5 - 0.5 * math.cos(2 * math.pi * t)
    amt = 2.0 * breath
    for i, r in enumerate(rays):
        cxr, cyr = r["centroid"]
        rel = r["pixels_rel"]
        bx, by = cxr - core_cx, cyr - core_cy
        a = math.radians(off)
        cosr, sinr = math.cos(a), math.sin(a)
        rx = bx * cosr - by * sinr
        ry = bx * sinr + by * cosr
        rot_rel = [(p[0]*cosr - p[1]*sinr, p[0]*sinr + p[1]*cosr) for p in rel]
        dx, dy = cxr - core_cx, cyr - core_cy
        L = math.hypot(dx, dy) or 1
        tx, ty = dx / L * amt, dy / L * amt
        ncx, ncy = core_cx + rx + tx, core_cy + ry + ty
        for p0, p1 in rot_rel:
            ix, iy = int(round(ncx + p0)), int(round(ncy + p1))
            if 0 <= ix < SIZE and 0 <= iy < SIZE:
                grid[iy][ix] = 255
    return grid


def flat(g):
    return [g[y][x] for y in range(SIZE) for x in range(SIZE)]


def diff(a, b):
    return sum(1 for x, y in zip(a, b) if (x > 0) != (y > 0))


# frame[0] and frame[FRAMES] should be identical (seamless)
f0 = flat(make_frame(0.0, 0.0))
f12 = flat(make_frame(1.0, 45.0))
print(f"seam diff frame[0] vs frame[12]: {diff(f0, f12)} pixels differ")

# per-frame ON count + how many "isolated" (potentially blurry) pixels
for i in range(FRAMES):
    t = i / FRAMES
    off = i * (45.0 / FRAMES)
    g = make_frame(t, off)
    fl = flat(g)
    on = sum(1 for v in fl if v > 0)
    print(f"frame {i:2d}: off={off:6.2f}deg  ON={on}")
