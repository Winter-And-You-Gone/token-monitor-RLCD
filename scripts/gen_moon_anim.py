#!/usr/bin/env python3
"""Generate the 17-frame binary moon overlay animation for icons.c.

The UI draws a white circular moon body with a thin black rim. These A8
frames are black overlays: the moving phase shadow plus fixed crater shadows.
Frame 0 preserves the existing crescent silhouette by inverting its pixels
inside the analytic disk, and frame 16 duplicates frame 0 for a closed loop.
"""
from pathlib import Path
import math
import re

ROOT = Path(__file__).resolve().parents[1]
ICONS_C = ROOT / "firmware" / "components" / "ui_app" / "icons.c"
ICONS_H = ROOT / "firmware" / "components" / "ui_app" / "icons.h"
SIZE = 48
FRAME_STEPS = 16
N = FRAME_STEPS + 1

# The RLCD ultimately displays one bit per pixel. These A8 frames are
# recolored black by mkicon: 255 is a black phase/crater overlay and 0 is
# transparent, revealing the static white moon body beneath it.
src = ICONS_C.read_text(encoding="utf-8", errors="replace")
moon_match = re.search(r"static const uint8_t moon_map\[\]\s*=\s*\{([^}]*)\};", src)
if not moon_match:
    raise SystemExit("moon_map[] not found in icons.c")
raw_moon = [int(x) for x in re.findall(r"\d+", moon_match.group(1))]
if len(raw_moon) != SIZE * SIZE:
    raise SystemExit(f"moon_map has {len(raw_moon)} bytes, expected {SIZE * SIZE}")
if any(value not in (0, 255) for value in raw_moon):
    raise SystemExit("moon_map must contain only 0/255 A8 values")
existing = {
    (x, y)
    for y in range(SIZE)
    for x in range(SIZE)
    if raw_moon[y * SIZE + x] == 255
}

CX, CY = 24.5, 24
R = 20.5
DX_FULL = 2 * R + 1
PHI0 = 5 * math.pi / 3


def disk_pixels():
    return {
        (x, y)
        for y in range(SIZE)
        for x in range(SIZE)
        if math.hypot(x - CX, y - CY) <= R
    }


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
    # Hand-drawn lower silhouette: thicker, broken, and slightly lopsided.
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


# Fixed surface landmarks. Each tuple is (anchor, black-shadow offsets).
# Hollow and broken rings imitate the reference icon's crater walls while the
# small pocks keep scale variation. Every glyph is phase-gated below so it
# stays away from both the outer limb and the moving terminator.
CRATER_GLYPHS = (
    # Reference-like large hollow depressions with a heavy lower/right shadow.
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
    # Small solid pits from the reference silhouette.
    ((26, 5), ((0, 0), (1, 0), (0, 1), (1, 1))),
    ((35, 18), ((0, 0), (1, 0), (0, 1))),
    ((16, 34), ((0, 0), (1, 0), (2, 0), (1, 1))),
    ((38, 27), ((0, 0), (0, 1), (1, 1))),
    ((8, 35), ((0, 0), (1, 0), (1, 1), (2, 1))),
    ((34, 37), ((0, 0), (1, 0), (2, 0), (1, 1))),
)


def crater_shadows(lit):
    """Return complete crater glyphs safely inside the current white surface."""
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


def render_overlay(points):
    data = [0] * (SIZE * SIZE)
    for x, y in points:
        data[y * SIZE + x] = 255
    return data


frames_bytes = []
shadow_counts = []
crater_counts = []
for i in range(FRAME_STEPS):
    if i == 0:
        # Preserve the original C-shaped first frame as the white lit surface.
        phase_shadow = DISK - existing
        craters = crater_shadows(existing)
    else:
        phi = PHI0 + 2 * math.pi * i / FRAME_STEPS
        base_lit = lit_for_dx(dx_for_k_side(k_of(phi), lit_side(phi)))
        phase_shadow = DISK - base_lit
        craters = crater_shadows(base_lit)
    frame = render_overlay(phase_shadow | craters)
    frames_bytes.append(frame)
    shadow_counts.append(len(phase_shadow))
    crater_counts.append(len(craters))

# The duplicate endpoint intentionally has identical overlay pixels.
frames_bytes.append(list(frames_bytes[0]))
shadow_counts.append(shadow_counts[0])
crater_counts.append(crater_counts[0])

if frames_bytes[FRAME_STEPS] != frames_bytes[0]:
    raise SystemExit("moon animation seam is not frame-identical")
if any(value not in (0, 255) for frame in frames_bytes for value in frame):
    raise SystemExit("generated moon animation contains non-binary A8 data")


def emit_frame(index, data):
    values = ",".join(str(value) for value in data)
    return (
        f"static const uint8_t moon_anim_f{index}_map[] = {{{values}}};\n"
        f"const lv_image_dsc_t icon_moon_anim_f{index} = {{\n"
        "  .header = { .magic = LV_IMAGE_HEADER_MAGIC, .cf = LV_COLOR_FORMAT_A8,\n"
        f"               .flags = 0, .w = {SIZE}, .h = {SIZE}, .stride = {SIZE} }},\n"
        f"  .data_size = {SIZE * SIZE}, .data = moon_anim_f{index}_map,\n"
        "};\n"
    )


def emit_rim():
    values = ",".join(
        "255" if (x, y) in RIM else "0"
        for y in range(SIZE)
        for x in range(SIZE)
    )
    return (
        f"static const uint8_t moon_rim_map[] = {{{values}}};\\n"
        "const lv_image_dsc_t icon_moon_rim = {\\n"
        "  .header = { .magic = LV_IMAGE_HEADER_MAGIC, .cf = LV_COLOR_FORMAT_A8,\\n"
        f"               .flags = 0, .w = {SIZE}, .h = {SIZE}, .stride = {SIZE} }},\\n"
        f"  .data_size = {SIZE * SIZE}, .data = moon_rim_map,\\n"
        "};\\n"
    )


block_lines = [
    "/* === BEGIN moon animation (generated by scripts/gen_moon_anim.py) === */",
    "",
    emit_rim(),
    "",
]
for index, data in enumerate(frames_bytes):
    block_lines.append(emit_frame(index, data))
    block_lines.append("")
block_lines.append(
    "const lv_image_dsc_t *icon_moon_anim[] = {"
    + ", ".join(f"&icon_moon_anim_f{index}" for index in range(N))
    + "};"
)
block_lines.append(f"const int icon_moon_anim_count = {N};")
block_lines.extend(["", "/* === END moon animation === */", ""])
block = "\n".join(block_lines)

marker = re.compile(
    r"/\* === BEGIN moon animation.*?=== END moon animation === \*/\n?",
    re.DOTALL,
)
c_src = ICONS_C.read_text(encoding="utf-8", errors="replace")
if marker.search(c_src):
    c_src = marker.sub(block.rstrip("\n"), c_src)
else:
    c_src = c_src.rstrip() + "\n\n" + block
ICONS_C.write_text(c_src, encoding="utf-8")

h_src = ICONS_H.read_text(encoding="utf-8")
h_decl = (
    "/* moon body rim and animation frames (generated) */\n"
    "extern const lv_image_dsc_t icon_moon_rim;\n"
    "extern const lv_image_dsc_t *icon_moon_anim[];\n"
    "extern const int icon_moon_anim_count;\n"
)
h_marker = re.compile(
    r"/\* moon (?:body rim and )?animation frames.*?extern const int icon_moon_anim_count;\n",
    re.DOTALL,
)
if h_marker.search(h_src):
    h_src = h_marker.sub(h_decl, h_src)
else:
    h_src = h_src.rstrip() + "\n" + h_decl
ICONS_H.write_text(h_src, encoding="utf-8")

print(f"wrote {N} frames to icons.c / icons.h ({SIZE}x{SIZE} binary A8 overlays)")
print("phase shadow pixels:", shadow_counts)
print("crater shadow pixels:", crater_counts)
print("frame0 == frame16:", frames_bytes[0] == frames_bytes[FRAME_STEPS])
