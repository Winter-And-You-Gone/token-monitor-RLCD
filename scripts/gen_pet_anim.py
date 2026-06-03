#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageSequence

ROOT = Path(__file__).resolve().parents[1]
GIF_DIR = ROOT / "clawd-on-desk" / "assets" / "gif"
OUT_C = ROOT / "firmware" / "components" / "ui_app" / "pet_anim.c"
OUT_H = ROOT / "firmware" / "components" / "ui_app" / "pet_anim.h"

TARGET_SIZE = 56
PADDING = 4
FOCUS_PADDING = 10
BODY_NEAR_MARGIN = 28
ALPHA_THRESHOLD = 16
LUMA_THRESHOLD = 238
EYE_LUMA_THRESHOLD = 90
DEFAULT_DURATION_MS = 80

STATE_TO_ASSET = {
    "idle": "clawd-idle-follow.svg",
    "yawning": "clawd-idle-yawn.svg",
    "dozing": "clawd-idle-doze.svg",
    "collapsing": "clawd-collapse-sleep.svg",
    "thinking": "clawd-working-thinking.svg",
    "working": "clawd-working-typing.svg",
    "juggling": "clawd-working-juggling.svg",
    "sweeping": "clawd-working-sweeping.svg",
    "error": "clawd-error.svg",
    "attention": "clawd-happy.svg",
    "notification": "clawd-notification.svg",
    "carrying": "clawd-working-carrying.svg",
    "sleeping": "clawd-sleeping.svg",
    "waking": "clawd-wake.svg",
}

ASSET_TO_GIF = {
    "clawd-idle-follow.svg": "clawd-idle.gif",
    "clawd-idle-yawn.svg": "clawd-idle-reading.gif",
    "clawd-idle-doze.svg": "clawd-sleeping.gif",
    "clawd-collapse-sleep.svg": "clawd-sleeping.gif",
    "clawd-working-thinking.svg": "clawd-thinking.gif",
    "clawd-working-typing.svg": "clawd-typing.gif",
    "clawd-headphones-groove.svg": "clawd-headphones-groove.gif",
    "clawd-working-juggling.svg": "clawd-juggling.gif",
    "clawd-working-sweeping.svg": "clawd-sweeping.gif",
    "clawd-error.svg": "clawd-error.gif",
    "clawd-happy.svg": "clawd-happy.gif",
    "clawd-notification.svg": "clawd-notification.gif",
    "clawd-working-carrying.svg": "clawd-carrying.gif",
    "clawd-sleeping.svg": "clawd-sleeping.gif",
    "clawd-wake.svg": "clawd-mini-enter.gif",
    "clawd-working-building.svg": "clawd-building.gif",
}

EXTRA_ASSETS = {
    "clawd-working-juggling.svg",
    "clawd-working-building.svg",
}
IDLE_ASSET = STATE_TO_ASSET["idle"]


def sanitize(name: str) -> str:
    out: list[str] = []
    last_underscore = False
    for ch in name.lower():
        if ch.isalnum():
            out.append(ch)
            last_underscore = False
        elif not last_underscore:
            out.append("_")
            last_underscore = True
    return "".join(out).strip("_") or "pet"


def chunked(data: bytes, size: int = 16) -> list[str]:
    return [
        ", ".join(str(b) for b in data[start : start + size])
        for start in range(0, len(data), size)
    ]


def load_gif_frames(gif_path: Path) -> tuple[list[Image.Image], list[int]]:
    image = Image.open(gif_path)
    frames: list[Image.Image] = []
    durations: list[int] = []
    for frame in ImageSequence.Iterator(image):
        rgba = frame.convert("RGBA")
        frames.append(rgba.copy())
        duration = int(frame.info.get("duration") or image.info.get("duration") or DEFAULT_DURATION_MS)
        durations.append(duration if duration > 0 else DEFAULT_DURATION_MS)
    return frames, durations


def ink_mask(frame: Image.Image) -> Image.Image:
    rgba = frame.convert("RGBA")
    alpha = rgba.getchannel("A")
    luma = ImageOps.grayscale(rgba.convert("RGB"))
    alpha_mask = alpha.point(lambda value: 255 if value >= ALPHA_THRESHOLD else 0)
    ink = luma.point(lambda value: 255 if value < LUMA_THRESHOLD else 0)
    return ImageChops.multiply(alpha_mask, ink)


def _is_body_orange(r: int, g: int, b: int, a: int) -> bool:
    return (
        a >= ALPHA_THRESHOLD
        and r >= 165
        and 70 <= g <= 190
        and b <= 170
        and r - g >= 20
        and g - b >= 5
    )


def _connected_components(points: set[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    seen: set[tuple[int, int]] = set()
    components: list[list[tuple[int, int]]] = []
    for point in points:
        if point in seen:
            continue
        stack = [point]
        seen.add(point)
        component: list[tuple[int, int]] = []
        while stack:
            x, y = stack.pop()
            component.append((x, y))
            for nx in (x - 1, x, x + 1):
                for ny in (y - 1, y, y + 1):
                    neighbor = (nx, ny)
                    if neighbor == (x, y) or neighbor in seen or neighbor not in points:
                        continue
                    seen.add(neighbor)
                    stack.append(neighbor)
        components.append(component)
    return components


def _component_bbox(component: list[tuple[int, int]]) -> tuple[int, int, int, int]:
    xs = [point[0] for point in component]
    ys = [point[1] for point in component]
    return min(xs), min(ys), max(xs), max(ys)


def _expand_bbox(
    bbox: tuple[int, int, int, int],
    margin: int,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bbox
    return (
        max(0, x0 - margin),
        max(0, y0 - margin),
        min(width - 1, x1 + margin),
        min(height - 1, y1 + margin),
    )


def _bbox_intersects(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _body_focus_bbox(frame: Image.Image) -> tuple[int, int, int, int] | None:
    rgba = frame.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    orange_points: set[tuple[int, int]] = set()

    for y in range(height):
        for x in range(width):
            if _is_body_orange(*pixels[x, y]):
                orange_points.add((x, y))

    components = _connected_components(orange_points)
    if not components:
        return None

    main_component = max(components, key=len)
    main_bbox = _component_bbox(main_component)
    focus_area = _expand_bbox(main_bbox, BODY_NEAR_MARGIN, width, height)
    focus_points = list(main_component)

    for component in components:
        if component is main_component:
            continue
        bbox = _component_bbox(component)
        if _bbox_intersects(bbox, focus_area):
            focus_points.extend(component)

    x0, y0, x1, y1 = _component_bbox(focus_points)
    return (
        max(0, x0 - FOCUS_PADDING),
        max(0, y0 - FOCUS_PADDING),
        min(width, x1 + 1 + FOCUS_PADDING),
        min(height, y1 + 1 + FOCUS_PADDING),
    )


def eye_knockout_mask(canvas: Image.Image) -> Image.Image:
    rgba = canvas.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    orange_points: set[tuple[int, int]] = set()
    dark_points: set[tuple[int, int]] = set()

    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if _is_body_orange(r, g, b, a):
                orange_points.add((x, y))
            luma = int(0.299 * r + 0.587 * g + 0.114 * b)
            if a >= ALPHA_THRESHOLD and luma < EYE_LUMA_THRESHOLD:
                dark_points.add((x, y))

    orange_components = _connected_components(orange_points)
    if not orange_components:
        return Image.new("L", (width, height), 0)
    body = max(orange_components, key=len)
    body_x0, body_y0, body_x1, body_y1 = _component_bbox(body)

    eye_points: list[tuple[int, int]] = []
    for component in _connected_components(dark_points):
        x0, y0, x1, y1 = _component_bbox(component)
        area = len(component)
        comp_w = x1 - x0 + 1
        comp_h = y1 - y0 + 1
        if area < 2 or area > 80 or comp_w > 10 or comp_h > 12:
            continue
        if x0 < body_x0 - 2 or x1 > body_x1 + 2 or y0 < body_y0 - 2 or y1 > body_y1 + 1:
            continue

        ex0 = max(0, x0 - 3)
        ex1 = min(width - 1, x1 + 3)
        ey0 = max(0, y0 - 3)
        ey1 = min(height - 1, y1 + 3)
        orange_nearby = sum(
            1
            for yy in range(ey0, ey1 + 1)
            for xx in range(ex0, ex1 + 1)
            if (xx, yy) in orange_points
        )
        if orange_nearby < max(8, area):
            continue
        eye_points.extend(component)

    mask = Image.new("L", (width, height), 0)
    if eye_points:
        draw = ImageDraw.Draw(mask)
        draw.point(eye_points, fill=255)
        mask = mask.filter(ImageFilter.MaxFilter(3))
    return mask


def union_bbox(frames: list[Image.Image]) -> tuple[int, int, int, int]:
    boxes = [_body_focus_bbox(frame) for frame in frames]
    boxes = [box for box in boxes if box]
    if not boxes:
        boxes = [ink_mask(frame).getbbox() for frame in frames]
    boxes = [box for box in boxes if box]
    if not boxes:
        return (0, 0, frames[0].width, frames[0].height) if frames else (0, 0, TARGET_SIZE, TARGET_SIZE)
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[2] for box in boxes)
    bottom = max(box[3] for box in boxes)
    return (
        max(0, left - PADDING),
        max(0, top - PADDING),
        min(frames[0].width, right + PADDING),
        min(frames[0].height, bottom + PADDING),
    )


def _frame_canvas(frame: Image.Image, bbox: tuple[int, int, int, int]) -> Image.Image:
    crop = frame.crop(bbox)
    fitted = ImageOps.contain(crop, (TARGET_SIZE, TARGET_SIZE), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (TARGET_SIZE, TARGET_SIZE), (255, 255, 255, 0))
    ox = (TARGET_SIZE - fitted.width) // 2
    oy = (TARGET_SIZE - fitted.height) // 2
    canvas.alpha_composite(fitted, (ox, oy))
    return canvas


def rasterize_frame_masks(frame: Image.Image, bbox: tuple[int, int, int, int]) -> tuple[bytes, bytes]:
    canvas = _frame_canvas(frame, bbox)
    eyes = eye_knockout_mask(canvas)
    body = ImageChops.subtract(ink_mask(canvas), eyes).convert("1", dither=Image.Dither.NONE)
    eye_mask = eyes.convert("1", dither=Image.Dither.NONE)
    return body.tobytes(), eye_mask.tobytes()


def rasterize_frame(frame: Image.Image, bbox: tuple[int, int, int, int]) -> bytes:
    mask, _ = rasterize_frame_masks(frame, bbox)
    return mask


def emit_frame(name: str, index: int, data: bytes) -> list[str]:
    stride = (TARGET_SIZE + 7) // 8
    return [
        f"static const uint8_t {name}_frame_{index}_map[] = {{",
        *("  " + line + "," for line in chunked(data)),
        "};",
        f"static const lv_image_dsc_t {name}_frame_{index} = {{",
        "  .header = { .magic = LV_IMAGE_HEADER_MAGIC, .cf = LV_COLOR_FORMAT_A1,",
        f"               .flags = 0, .w = {TARGET_SIZE}, .h = {TARGET_SIZE}, .stride = {stride} }},",
        f"  .data_size = {len(data)}, .data = {name}_frame_{index}_map,",
        "};",
        "",
    ]


def needed_gifs() -> dict[str, Path]:
    assets = set(STATE_TO_ASSET.values()) | EXTRA_ASSETS
    missing_assets = sorted(asset for asset in assets if asset not in ASSET_TO_GIF)
    if missing_assets:
        raise SystemExit(f"missing GIF mapping for: {', '.join(missing_assets)}")
    result: dict[str, Path] = {}
    for asset in sorted(assets):
        gif_name = ASSET_TO_GIF[asset]
        gif_path = GIF_DIR / gif_name
        if not gif_path.exists():
            raise SystemExit(f"missing source GIF: {gif_path}")
        result[gif_name] = gif_path
    return result


def main() -> None:
    gif_specs: dict[str, dict[str, object]] = {}
    for gif_name, gif_path in needed_gifs().items():
        frames, durations = load_gif_frames(gif_path)
        gif_specs[gif_name] = {
            "frames": frames,
            "durations": durations,
            "bbox": union_bbox(frames),
        }

    OUT_H.write_text(
        "\n".join(
            [
                "#pragma once",
                "",
                "#include <stdint.h>",
                "#include \"lvgl.h\"",
                "",
                "#ifdef __cplusplus",
                "extern \"C\" {",
                "#endif",
                "",
                "typedef struct {",
                "    const lv_image_dsc_t *const *frames;",
                "    const lv_image_dsc_t *const *eye_frames;",
                "    const uint16_t *durations_ms;",
                "    uint16_t frame_count;",
                "    uint16_t width;",
                "    uint16_t height;",
                "} pet_anim_sequence_t;",
                "",
                "const pet_anim_sequence_t *ui_pet_anim_for_asset(const char *asset);",
                "const pet_anim_sequence_t *ui_pet_anim_for_state(const char *state);",
                "const pet_anim_sequence_t *ui_pet_anim_idle(void);",
                "",
                "#ifdef __cplusplus",
                "}",
                "#endif",
                "",
            ]
        ),
        encoding="utf-8",
    )

    c_lines = [
        '#include "pet_anim.h"',
        "",
        "#include <stddef.h>",
        "#include <string.h>",
        "",
        "#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))",
        "",
    ]

    sequence_names: dict[str, str] = {}
    for gif_name, spec in gif_specs.items():
        sequence_name = sanitize(Path(gif_name).stem)
        sequence_names[gif_name] = sequence_name
        frames = spec["frames"]
        durations = spec["durations"]
        bbox = spec["bbox"]
        c_lines.append(f"// {gif_name}")
        for index, frame in enumerate(frames):
            body_data, eye_data = rasterize_frame_masks(frame, bbox)
            c_lines.extend(emit_frame(sequence_name, index, body_data))
            c_lines.extend(emit_frame(f"{sequence_name}_eyes", index, eye_data))
        c_lines.append(f"static const lv_image_dsc_t *const {sequence_name}_frames[] = {{")
        c_lines.append(
            "  " + ", ".join(f"&{sequence_name}_frame_{index}" for index in range(len(frames))) + ","
        )
        c_lines.append("};")
        c_lines.append(f"static const lv_image_dsc_t *const {sequence_name}_eye_frames[] = {{")
        c_lines.append(
            "  " + ", ".join(f"&{sequence_name}_eyes_frame_{index}" for index in range(len(frames))) + ","
        )
        c_lines.append("};")
        c_lines.append(f"static const uint16_t {sequence_name}_durations_ms[] = {{")
        c_lines.append("  " + ", ".join(str(duration) for duration in durations) + ",")
        c_lines.append("};")
        c_lines.append(f"static const pet_anim_sequence_t {sequence_name}_sequence = {{")
        c_lines.append(f"  .frames = {sequence_name}_frames,")
        c_lines.append(f"  .eye_frames = {sequence_name}_eye_frames,")
        c_lines.append(f"  .durations_ms = {sequence_name}_durations_ms,")
        c_lines.append(f"  .frame_count = (uint16_t) ARRAY_SIZE({sequence_name}_frames),")
        c_lines.append(f"  .width = {TARGET_SIZE},")
        c_lines.append(f"  .height = {TARGET_SIZE},")
        c_lines.append("};")
        c_lines.append("")

    idle_sequence_name = sequence_names[ASSET_TO_GIF[IDLE_ASSET]]
    c_lines.append(f"static const pet_anim_sequence_t *const PET_ANIM_IDLE = &{idle_sequence_name}_sequence;")
    c_lines.append("")
    c_lines.append("typedef struct {")
    c_lines.append("    const char *asset;")
    c_lines.append("    const pet_anim_sequence_t *sequence;")
    c_lines.append("} pet_anim_asset_map_t;")
    c_lines.append("")
    c_lines.append("static const pet_anim_asset_map_t PET_ANIM_ASSET_MAP[] = {")
    for asset_name in sorted(set(STATE_TO_ASSET.values()) | EXTRA_ASSETS):
        gif_name = ASSET_TO_GIF[asset_name]
        c_lines.append(f'    {{"{asset_name}", &{sequence_names[gif_name]}_sequence}},')
    c_lines.append("};")
    c_lines.append("")
    c_lines.append("static const struct {")
    c_lines.append("    const char *state;")
    c_lines.append("    const char *asset;")
    c_lines.append("} PET_ANIM_STATE_MAP[] = {")
    for state, asset in STATE_TO_ASSET.items():
        c_lines.append(f'    {{"{state}", "{asset}"}},')
    c_lines.append("};")
    c_lines.append("")
    c_lines.append("static const pet_anim_sequence_t *lookup_asset(const char *asset)")
    c_lines.append("{")
    c_lines.append("    if (!asset || !asset[0]) return PET_ANIM_IDLE;")
    c_lines.append("    for (size_t i = 0; i < ARRAY_SIZE(PET_ANIM_ASSET_MAP); ++i) {")
    c_lines.append("        if (strcmp(PET_ANIM_ASSET_MAP[i].asset, asset) == 0) {")
    c_lines.append("            return PET_ANIM_ASSET_MAP[i].sequence;")
    c_lines.append("        }")
    c_lines.append("    }")
    c_lines.append("    return PET_ANIM_IDLE;")
    c_lines.append("}")
    c_lines.append("")
    c_lines.append("const pet_anim_sequence_t *ui_pet_anim_for_asset(const char *asset)")
    c_lines.append("{")
    c_lines.append("    return lookup_asset(asset);")
    c_lines.append("}")
    c_lines.append("")
    c_lines.append("const pet_anim_sequence_t *ui_pet_anim_idle(void)")
    c_lines.append("{")
    c_lines.append("    return PET_ANIM_IDLE;")
    c_lines.append("}")
    c_lines.append("")
    c_lines.append("const pet_anim_sequence_t *ui_pet_anim_for_state(const char *state)")
    c_lines.append("{")
    c_lines.append("    if (!state || !state[0]) return ui_pet_anim_idle();")
    c_lines.append("    for (size_t i = 0; i < ARRAY_SIZE(PET_ANIM_STATE_MAP); ++i) {")
    c_lines.append("        if (strcmp(PET_ANIM_STATE_MAP[i].state, state) == 0) {")
    c_lines.append("            return lookup_asset(PET_ANIM_STATE_MAP[i].asset);")
    c_lines.append("        }")
    c_lines.append("    }")
    c_lines.append("    return ui_pet_anim_idle();")
    c_lines.append("}")
    c_lines.append("")

    OUT_C.write_text("\n".join(c_lines), encoding="utf-8")


if __name__ == "__main__":
    main()
