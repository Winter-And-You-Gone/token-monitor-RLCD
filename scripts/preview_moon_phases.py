#!/usr/bin/env python3
"""Preview and validate the white-body moon overlay animation.

The firmware draws a white circular body with a one-pixel black rim, then
recolors each A8 frame black. A frame's 255 pixels therefore represent the
moving phase shadow and fixed crater shadows, while 0 remains transparent.
"""
from pathlib import Path
import math
import re

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ICONS_C = ROOT / "firmware" / "components" / "ui_app" / "icons.c"
OUT = ROOT / "docs" / "preview"
SIZE = 48
SCALE = 10
FRAME_STEPS = 16
FRAME_COUNT = FRAME_STEPS + 1
CX, CY = 24.5, 24
R = 20.5
DX_FULL = 2 * R + 1
PHI0 = 5 * math.pi / 3


def parse_array(source, name):
    match = re.search(
        rf"{re.escape(name)}\[\]\s*=\s*\{{([^}}]*)\}};",
        source,
        re.DOTALL,
    )
    if not match:
        raise AssertionError(f"missing {name}[]")
    return [int(value) for value in re.findall(r"\d+", match.group(1))]


source = ICONS_C.read_text(encoding="utf-8", errors="replace")
raw_moon = parse_array(source, "moon_map")
if len(raw_moon) != SIZE * SIZE:
    raise AssertionError(f"moon_map size {len(raw_moon)} != {SIZE * SIZE}")
if any(value not in (0, 255) for value in raw_moon):
    raise AssertionError("moon_map contains non-binary A8 data")
raw_rim = parse_array(source, "moon_rim_map")
if len(raw_rim) != SIZE * SIZE:
    raise AssertionError(f"moon_rim_map size {len(raw_rim)} != {SIZE * SIZE}")
if any(value not in (0, 255) for value in raw_rim):
    raise AssertionError("moon_rim_map contains non-binary A8 data")

frames = []
for index in range(FRAME_COUNT):
    data = parse_array(source, f"moon_anim_f{index}_map")
    if len(data) != SIZE * SIZE:
        raise AssertionError(f"frame {index} size {len(data)} != {SIZE * SIZE}")
    if any(value not in (0, 255) for value in data):
        raise AssertionError(f"frame {index} contains non-binary A8 data")
    frames.append(data)

if frames[FRAME_STEPS] != frames[0]:
    raise AssertionError("frame 16 does not equal frame 0")


def disk_pixels():
    return {
        (x, y)
        for y in range(SIZE)
        for x in range(SIZE)
        if math.hypot(x - CX, y - CY) <= R
    }


def shadow_disk(dx):
    sx = CX + dx
    return {
        (x, y)
        for y in range(SIZE)
        for x in range(SIZE)
        if math.hypot(x - sx, y - CY) <= R
    }


def lit_for_dx(dx):
    if abs(dx) >= DX_FULL:
        return set(DISK)
    return DISK - shadow_disk(dx)


def k_of(phi):
    return (1 - math.cos(phi)) / 2


def lit_side(phi):
    phase = phi % (2 * math.pi)
    return "right" if 0 < phase < math.pi else "left"


def dx_for_k_side(k, side):
    magnitude = DX_FULL * k
    return magnitude if side == "left" else -magnitude


def neighbors8(point):
    x, y = point
    return {
        (x + dx, y + dy)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if (dx, dy) != (0, 0)
    }


DISK = disk_pixels()


def rim_pixels():
    rim = {
        point
        for point in DISK
        if any(neighbor not in DISK for neighbor in neighbors8(point))
    }
    rim.update({
        (15, 4), (16, 4), (17, 4), (18, 4), (30, 4), (31, 4), (32, 4),
        (6, 17), (5, 18), (5, 19), (42, 18), (43, 19), (43, 20),
        (8, 31), (8, 32), (9, 33), (10, 35), (11, 36), (12, 38),
        (10, 40), (11, 41), (12, 41), (13, 42), (14, 42), (15, 43),
        (16, 43), (17, 44), (18, 44), (19, 43), (20, 44), (21, 44),
        (22, 43), (23, 44), (24, 44), (25, 44), (26, 44), (27, 43),
        (28, 44), (29, 44), (30, 43), (31, 44), (32, 43), (33, 43),
        (34, 42), (35, 42), (36, 41), (37, 40), (38, 39), (39, 38),
        (40, 37), (41, 35), (42, 34),
    } & DISK)
    return rim


RIM = rim_pixels()
RIM_BYTES = {
    (index % SIZE, index // SIZE)
    for index, value in enumerate(raw_rim)
    if value == 255
}
if RIM_BYTES != RIM:
    raise AssertionError("moon_rim_map does not match the hand-drawn disk rim")
EXISTING = {
    (x, y)
    for y in range(SIZE)
    for x in range(SIZE)
    if raw_moon[y * SIZE + x] == 255
}

# Keep this table identical to gen_moon_anim.py. The preview validates the
# generated C arrays independently instead of importing the generator.
CRATER_GLYPHS = (
    ((10, 7), (
        (1, 1), (2, 0), (3, 0), (4, 1), (5, 2),
        (5, 3), (4, 4), (3, 5), (2, 5), (1, 4),
        (0, 3), (0, 2), (1, 2),
    )),
    ((25, 28), (
        (1, 1), (2, 0), (3, 0), (4, 1), (5, 2),
        (5, 3), (4, 4), (3, 5), (2, 5), (1, 4),
        (0, 3), (0, 2), (1, 2),
    )),
    ((28, 13), (
        (1, 0), (2, 0), (3, 1), (3, 2), (2, 3),
        (1, 3), (0, 2), (0, 1),
    )),
    ((8, 22), (
        (1, 0), (2, 0), (3, 1), (3, 2), (2, 3),
        (1, 3), (0, 2), (0, 1),
    )),
    ((26, 5), ((0, 0), (1, 0), (0, 1), (1, 1))),
    ((35, 18), ((0, 0), (1, 0), (0, 1))),
    ((16, 34), ((0, 0), (1, 0), (2, 0), (1, 1))),
    ((38, 27), ((0, 0), (0, 1), (1, 1))),
    ((8, 35), ((0, 0), (1, 0), (1, 1), (2, 1))),
    ((34, 37), ((0, 0), (1, 0), (2, 0), (1, 1))),
)


def crater_shadows(lit):
    safe = {
        point
        for point in lit
        if neighbors8(point) <= lit
    }
    shadows = set()
    for (ax, ay), offsets in CRATER_GLYPHS:
        glyph = {(ax + dx, ay + dy) for dx, dy in offsets}
        if glyph <= safe and not glyph & shadows:
            shadows.update(glyph)
    return shadows


def lit_from_bytes(data):
    return {
        (index % SIZE, index // SIZE)
        for index, value in enumerate(data)
        if value == 255
    }


def draw_moon(data):
    image = Image.new("L", (SIZE, SIZE), 255)
    draw = ImageDraw.Draw(image)
    bbox = (CX - R, CY - R, CX + R, CY + R)
    draw.ellipse(bbox, fill=255)
    for x, y in RIM_BYTES | lit_from_bytes(data):
        image.putpixel((x, y), 0)
    return image.resize((SIZE * SCALE, SIZE * SCALE), Image.Resampling.NEAREST)


base_masks = [EXISTING]
for index in range(1, FRAME_STEPS):
    phi = PHI0 + 2 * math.pi * index / FRAME_STEPS
    base_masks.append(lit_for_dx(dx_for_k_side(k_of(phi), lit_side(phi))))
base_masks.append(EXISTING)

phase_counts = []
crater_counts = []
for index, data in enumerate(frames):
    overlay = lit_from_bytes(data)
    if not overlay <= DISK:
        raise AssertionError(f"frame {index} has overlay pixels outside disk")

    base = base_masks[index]
    phase_shadow = DISK - base
    expected_craters = crater_shadows(base)
    actual_craters = overlay - phase_shadow
    if overlay != phase_shadow | expected_craters:
        raise AssertionError(f"frame {index} phase/crater overlay mismatch")
    if actual_craters & phase_shadow:
        raise AssertionError(f"frame {index} has ambiguous crater pixels")

    boundary = {
        point
        for point in base
        if any(neighbor not in base for neighbor in neighbors8(point))
    }
    if expected_craters & boundary:
        raise AssertionError(f"frame {index} crater touches phase boundary")

    phase_counts.append(len(phase_shadow))
    crater_counts.append(len(expected_craters))

if max(crater_counts[1:-1]) == 0:
    raise AssertionError("generated moon has no crater shadows")
if crater_counts[0] != crater_counts[FRAME_STEPS]:
    raise AssertionError("seam crater counts differ")

OUT.mkdir(parents=True, exist_ok=True)
contact = Image.new("L", (SIZE * SCALE * FRAME_COUNT, SIZE * SCALE), 255)
for index, data in enumerate(frames):
    image = draw_moon(data)
    image.save(OUT / f"moon_white_body_f{index}.png")
    contact.paste(image, (index * SIZE * SCALE, 0))
contact.save(OUT / "moon_white_body_contact.png")

print(f"validated {FRAME_COUNT} frames of {SIZE}x{SIZE} binary A8 overlays")
print("phase shadow pixels:", phase_counts)
print("crater shadow pixels:", crater_counts)
print("frame0 == frame16: True")
print(f"wrote {OUT / 'moon_white_body_contact.png'}")
