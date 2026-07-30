#!/usr/bin/env python3
"""Animate the REAL icon_sun pixels (extracted from icons.c) - no redrawing.
Core circle stays fixed; the 8 real ray-clusters are transformed per frame.
Two modes: PULSE (rays translate radially in/out) and ROTATE (rays orbit).
Outputs frame contact sheets for preview. Does NOT touch icons.c."""
from pathlib import Path
import json, math
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "preview"
SIZE = 48
S = 8  # upscale

data = json.loads((OUT / "sun_clusters.json").read_text())
core = data["core"]["pixels"]
rays = data["rays"]
core_cx, core_cy = data["core"]["centroid"]


def stamp(small_im, pts):
    px = small_im.load()
    for x, y in pts:
        ix, iy = int(round(x)), int(round(y))
        if 0 <= ix < SIZE and 0 <= iy < SIZE:
            px[ix, iy] = 255


def render_frame(ray_translate=None, ray_rotate_deg=0.0):
    """ray_translate: list of (dx,dy) per ray (radial pulse), or None.
    ray_rotate_deg: rotate each ray's position+pixels around core."""
    im = Image.new("L", (SIZE, SIZE), 0)
    # core stays fixed
    stamp(im, core)
    for i, r in enumerate(rays):
        cxr, cyr = r["centroid"]
        rel = r["pixels_rel"]
        # base position of this ray's centroid relative to core
        bx, by = cxr - core_cx, cyr - core_cy
        if ray_rotate_deg:
            a = math.radians(ray_rotate_deg)
            rx = bx * math.cos(a) - by * math.sin(a)
            ry = bx * math.sin(a) + by * math.cos(a)
            # also rotate the ray pixels
            cosr, sinr = math.cos(a), math.sin(a)
            rot_rel = [(p[0]*cosr - p[1]*sinr, p[0]*sinr + p[1]*cosr) for p in rel]
        else:
            rx, ry = bx, by
            rot_rel = rel
        tx, ty = 0.0, 0.0
        if ray_translate:
            tx, ty = ray_translate[i]
        new_cx = core_cx + rx + tx
        new_cy = core_cy + ry + ty
        stamp(im, [(new_cx + p[0], new_cy + p[1]) for p in rot_rel])
    return im.resize((SIZE * S, SIZE * S), Image.NEAREST)


# --- PULSE: each ray moves radially out then in ---
PULSE_FRAMES = 6
pulse = []
for f in range(PULSE_FRAMES):
    t = f / PULSE_FRAMES
    breath = 0.5 - 0.5 * math.cos(2 * math.pi * t)   # 0..1..0
    amt = 2.0 * breath                                 # up to 2px outward
    trans = []
    for r in rays:
        cxr, cyr = r["centroid"]
        dx, dy = cxr - core_cx, cyr - core_cy
        L = math.hypot(dx, dy) or 1
        trans.append((dx / L * amt, dy / L * amt))
    pulse.append(render_frame(ray_translate=trans))

# --- ROTATE: rays orbit around core ---
ROT_FRAMES = 8
rot = []
for f in range(ROT_FRAMES):
    off = f * (45.0 / ROT_FRAMES)   # 0..45deg over 8 frames (half-step smooth)
    rot.append(render_frame(ray_rotate_deg=off))

# --- COMBO: rotate + pulse simultaneously ---
COMBO_FRAMES = 12
combo = []
for f in range(COMBO_FRAMES):
    t = f / COMBO_FRAMES
    breath = 0.5 - 0.5 * math.cos(2 * math.pi * t)        # 0..1..0
    amt = 2.0 * breath                                      # pulse up to 2px
    off = f * (45.0 / COMBO_FRAMES)                         # rotate 0..45deg
    trans = []
    for r in rays:
        cxr, cyr = r["centroid"]
        dx, dy = cxr - core_cx, cyr - core_cy
        L = math.hypot(dx, dy) or 1
        trans.append((dx / L * amt, dy / L * amt))
    combo.append(render_frame(ray_translate=trans, ray_rotate_deg=off))


def contact(frames, name):
    n = len(frames)
    sheet = Image.new("L", (SIZE * S * n, SIZE * S), 255)
    for i, f in enumerate(frames):
        sheet.paste(f, (i * SIZE * S, 0))
    sheet.save(OUT / name)


contact(pulse, "sun_anim_PULSE3_contact.png")
contact(rot, "sun_anim_ROTATE3_contact.png")
contact(combo, "sun_anim_COMBO_contact.png")
render_frame().save(OUT / "sun_real_repro.png")
print("wrote PULSE3, ROTATE3, COMBO, sun_real_repro")
