#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageSequence

ROOT = Path(__file__).resolve().parents[1]
GIF_DIR = ROOT / "clawd-on-desk" / "assets" / "gif"
# Re-rendered GIFs land here (only the ones that changed). Preferred over
# GIF_DIR so firmware frames use the latest art without touching the upstream
# clawd-on-desk source. See AGENTS.md "更新 gif 动画".
NEWGIF_DIR = ROOT / "bridge" / "assets" / "newgif"
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
THINKING_MIN_FULL_CYCLE_MS = 6000

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
    "clawd-idle-reading.svg": "clawd-idle-reading.gif",
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
    "clawd-idle-reading.svg",
    "clawd-working-juggling.svg",
    "clawd-working-building.svg",
    "clawd-headphones-groove.svg",
}
IDLE_ASSET = STATE_TO_ASSET["idle"]
FULL_INK_BBOX_GIFS = {
    "clawd-carrying.gif",
    "clawd-error.gif",
    "clawd-happy.gif",
    "clawd-headphones-groove.gif",
    "clawd-idle-reading.gif",
    "clawd-juggling.gif",
    "clawd-sleeping.gif",
    "clawd-thinking.gif",
    "clawd-typing.gif",
}


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
    return complete_short_thinking_cycle(gif_path.name, frames, durations)


def complete_short_thinking_cycle(
    gif_name: str,
    frames: list[Image.Image],
    durations: list[int],
) -> tuple[list[Image.Image], list[int]]:
    if gif_name != "clawd-thinking.gif" or not frames:
        return frames, durations
    if sum(durations) >= THINKING_MIN_FULL_CYCLE_MS:
        return frames, durations
    mirrored = [ImageOps.mirror(frame) for frame in frames]
    return frames + mirrored, durations + list(durations)


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


def _target_scale() -> float:
    return max(1.0, TARGET_SIZE / 56.0)


def _scaled(value: float, minimum: int = 1) -> int:
    return max(minimum, int(round(value * _target_scale())))


def _scaled_area(value: float, minimum: int = 1) -> int:
    scale = _target_scale()
    return max(minimum, int(round(value * scale * scale)))


def _odd(value: int) -> int:
    return value if value % 2 else value + 1


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
    max_eye_area = _scaled(80 * _target_scale(), 8)
    max_eye_w = _scaled(10, 4)
    max_eye_h = _scaled(12, 4)
    bbox_slop = _scaled(2, 2)
    nearby_margin = _scaled(3, 2)
    nearby_min = _scaled(8, 4)
    for component in _connected_components(dark_points):
        x0, y0, x1, y1 = _component_bbox(component)
        area = len(component)
        comp_w = x1 - x0 + 1
        comp_h = y1 - y0 + 1
        if area < 2 or area > max_eye_area or comp_w > max_eye_w or comp_h > max_eye_h:
            continue
        if (
            x0 < body_x0 - bbox_slop
            or x1 > body_x1 + bbox_slop
            or y0 < body_y0 - bbox_slop
            or y1 > body_y1 + bbox_slop
        ):
            continue

        ex0 = max(0, x0 - nearby_margin)
        ex1 = min(width - 1, x1 + nearby_margin)
        ey0 = max(0, y0 - nearby_margin)
        ey1 = min(height - 1, y1 + nearby_margin)
        orange_nearby = sum(
            1
            for yy in range(ey0, ey1 + 1)
            for xx in range(ex0, ex1 + 1)
            if (xx, yy) in orange_points
        )
        if orange_nearby < max(nearby_min, area):
            continue
        eye_points.extend(component)

    mask = Image.new("L", (width, height), 0)
    if eye_points:
        draw = ImageDraw.Draw(mask)
        draw.point(eye_points, fill=255)
        mask = mask.filter(ImageFilter.MaxFilter(3))
    return mask


def shadow_knockout_mask(canvas: Image.Image) -> Image.Image:
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
    body_x0, _body_y0, body_x1, body_y1 = _component_bbox(body)

    shadow_points: list[tuple[int, int]] = []
    min_shadow_w = _scaled(18, 10)
    max_shadow_h = _scaled(5, 5)
    y_slop = _scaled(2, 1)
    for component in _connected_components(dark_points):
        x0, y0, x1, y1 = _component_bbox(component)
        comp_w = x1 - x0 + 1
        comp_h = y1 - y0 + 1
        overlap_w = min(x1, body_x1) - max(x0, body_x0) + 1
        if (
            y0 >= body_y1 - y_slop
            and comp_w >= min_shadow_w
            and comp_h <= max_shadow_h
            and overlap_w >= min(comp_w, body_x1 - body_x0 + 1) * 0.45
        ):
            shadow_points.extend(component)

    mask = Image.new("L", (width, height), 0)
    if shadow_points:
        ImageDraw.Draw(mask).point(shadow_points, fill=255)
    return mask


def thinking_bubble_ink_mask(canvas: Image.Image) -> Image.Image:
    rgba = canvas.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    orange_points: set[tuple[int, int]] = set()
    light_points: set[tuple[int, int]] = set()

    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a < ALPHA_THRESHOLD:
                continue
            if _is_body_orange(r, g, b, a):
                orange_points.add((x, y))
            luma = int(0.299 * r + 0.587 * g + 0.114 * b)
            if luma >= 208:
                light_points.add((x, y))

    orange_components = _connected_components(orange_points)
    if not orange_components:
        return Image.new("L", (width, height), 0)
    body = max(orange_components, key=len)
    body_x0, body_y0, body_x1, _body_y1 = _component_bbox(body)

    bubble_fill = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(bubble_fill)
    min_area = _scaled_area(3, 3)
    min_outline_area = _scaled_area(16, 8)
    x_slop = _scaled(22, 8)
    y_slop = _scaled(3, 1)
    for component in _connected_components(light_points):
        x0, y0, x1, y1 = _component_bbox(component)
        if len(component) < min_area:
            continue
        if y0 > body_y0 + y_slop or y1 >= body_y0 + _scaled(8, 3):
            continue
        if x1 < body_x0 - x_slop or x0 > body_x1 + x_slop:
            continue
        draw.point(component, fill=255)

    if not bubble_fill.getbbox():
        return bubble_fill

    outline_width = _odd(_scaled(3, 3))
    outline = ImageChops.subtract(
        bubble_fill.filter(ImageFilter.MaxFilter(outline_width)),
        bubble_fill.filter(ImageFilter.MinFilter(outline_width)),
    )

    # Tiny thought dots are too small to survive as hollow outlines at 56px.
    solid_dots = Image.new("L", (width, height), 0)
    dot_draw = ImageDraw.Draw(solid_dots)
    for component in _connected_components(light_points):
        x0, y0, x1, y1 = _component_bbox(component)
        area = len(component)
        if area < min_area or area >= min_outline_area:
            continue
        if y0 > body_y0 + y_slop or y1 >= body_y0 + _scaled(8, 3):
            continue
        if x1 < body_x0 - x_slop or x0 > body_x1 + x_slop:
            continue
        dot_draw.point(component, fill=255)
    if solid_dots.getbbox():
        solid_dots = solid_dots.filter(ImageFilter.MaxFilter(_odd(_scaled(2, 1))))
        outline = ImageChops.lighter(outline, solid_dots)

    return outline


def typing_screen_line_knockout_mask(canvas: Image.Image) -> Image.Image:
    rgba = canvas.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    orange_points: set[tuple[int, int]] = set()
    dark_points: set[tuple[int, int]] = set()

    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a < ALPHA_THRESHOLD:
                continue
            if _is_body_orange(r, g, b, a):
                orange_points.add((x, y))
            luma = int(0.299 * r + 0.587 * g + 0.114 * b)
            if luma < 92:
                dark_points.add((x, y))

    orange_components = _connected_components(orange_points)
    if not orange_components:
        return Image.new("L", (width, height), 0)
    body = max(orange_components, key=len)
    _body_x0, body_y0, _body_x1, _body_y1 = _component_bbox(body)

    monitor_bbox: tuple[int, int, int, int] | None = None
    monitor_area = 0
    min_w = _scaled(16, 8)
    min_h = _scaled(12, 6)
    for component in _connected_components(dark_points):
        x0, y0, x1, y1 = _component_bbox(component)
        comp_w = x1 - x0 + 1
        comp_h = y1 - y0 + 1
        if y1 >= body_y0 or comp_w < min_w or comp_h < min_h:
            continue
        if len(component) > monitor_area:
            monitor_area = len(component)
            monitor_bbox = (x0, y0, x1, y1)

    mask = Image.new("L", (width, height), 0)
    if not monitor_bbox:
        return mask

    mx0, my0, mx1, my1 = monitor_bbox
    inset = _scaled(2, 1)
    line_points: set[tuple[int, int]] = set()
    for y in range(my0 + inset, my1 - inset + 1):
        for x in range(mx0 + inset, mx1 - inset + 1):
            r, g, b, a = pixels[x, y]
            if a < ALPHA_THRESHOLD or _is_body_orange(r, g, b, a):
                continue
            luma = int(0.299 * r + 0.587 * g + 0.114 * b)
            saturation = max(r, g, b) - min(r, g, b)
            if saturation >= 24 and luma >= 48:
                line_points.add((x, y))

    min_line_area = _scaled_area(2, 2)
    min_line_w = _scaled(2, 1)
    draw = ImageDraw.Draw(mask)
    for component in _connected_components(line_points):
        x0, _y0, x1, _y1 = _component_bbox(component)
        if len(component) >= min_line_area and x1 - x0 + 1 >= min_line_w:
            draw.point(component, fill=255)

    if mask.getbbox():
        clip = Image.new("L", (width, height), 0)
        ImageDraw.Draw(clip).rectangle((mx0 + inset, my0 + inset, mx1 - inset, my1 - inset), fill=255)
        mask = ImageChops.multiply(mask, clip)
    return mask


def typing_shadow_knockout_mask(canvas: Image.Image) -> Image.Image:
    rgba = canvas.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    orange_points: set[tuple[int, int]] = set()

    for y in range(height):
        for x in range(width):
            if _is_body_orange(*pixels[x, y]):
                orange_points.add((x, y))

    orange_components = _connected_components(orange_points)
    if not orange_components:
        return Image.new("L", (width, height), 0)
    body = max(orange_components, key=len)
    body_x0, _body_y0, body_x1, body_y1 = _component_bbox(body)

    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    x0 = max(0, body_x0 + _scaled(5, 2))
    x1 = min(width - 1, body_x1 - _scaled(5, 2))
    y0 = min(height - 1, body_y1 + _scaled(7, 3))
    for y in range(y0, height):
        for x in range(x0, x1 + 1):
            r, g, b, a = pixels[x, y]
            if a < ALPHA_THRESHOLD:
                continue
            luma = int(0.299 * r + 0.587 * g + 0.114 * b)
            if luma < 132:
                draw.point((x, y), fill=255)
    return mask


def typing_keyboard_separator_knockout_mask(canvas: Image.Image) -> Image.Image:
    if TARGET_SIZE < 100:
        return Image.new("L", (TARGET_SIZE, TARGET_SIZE), 0)

    rgba = canvas.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    orange_points: set[tuple[int, int]] = set()

    for y in range(height):
        for x in range(width):
            if _is_body_orange(*pixels[x, y]):
                orange_points.add((x, y))

    orange_components = _connected_components(orange_points)
    if not orange_components:
        return Image.new("L", (width, height), 0)
    body = max(orange_components, key=len)
    body_x0, _body_y0, body_x1, body_y1 = _component_bbox(body)

    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    x0 = max(0, body_x0 + 4)
    x1 = min(width - 1, body_x1 - 4)
    y = min(height - 1, body_y1 + 1)
    draw.line((x0, y, x1, y), fill=255, width=1)
    return mask


def rlcd_bw_mask_layers(
    canvas: Image.Image,
    gif_name: str | None = None,
) -> tuple[Image.Image, Image.Image, Image.Image, Image.Image]:
    eyes = eye_knockout_mask(canvas)
    shadows = shadow_knockout_mask(canvas)
    extra_ink = Image.new("L", canvas.size, 0)
    extra_white = Image.new("L", canvas.size, 0)

    if gif_name == "clawd-thinking.gif":
        extra_ink = ImageChops.lighter(extra_ink, thinking_bubble_ink_mask(canvas))
    if gif_name == "clawd-typing.gif":
        extra_white = ImageChops.lighter(extra_white, typing_screen_line_knockout_mask(canvas))
        extra_white = ImageChops.lighter(extra_white, typing_shadow_knockout_mask(canvas))
        extra_white = ImageChops.lighter(extra_white, typing_keyboard_separator_knockout_mask(canvas))

    white = ImageChops.lighter(eyes, shadows)
    white = ImageChops.lighter(white, extra_white)
    ink = ImageChops.lighter(ink_mask(canvas), extra_ink)
    black = ImageChops.subtract(ink, white)
    return black, white, ink, eyes


def union_bbox(frames: list[Image.Image], *, include_full_ink: bool = False) -> tuple[int, int, int, int]:
    boxes = [_body_focus_bbox(frame) for frame in frames]
    if include_full_ink:
        boxes.extend(ink_mask(frame).getbbox() for frame in frames)
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


def rasterize_frame_masks(
    frame: Image.Image,
    bbox: tuple[int, int, int, int],
    gif_name: str | None = None,
) -> tuple[bytes, bytes]:
    canvas = _frame_canvas(frame, bbox)
    black, _white, _ink, eyes = rlcd_bw_mask_layers(canvas, gif_name)
    body = black.convert("1", dither=Image.Dither.NONE)
    eye_mask = eyes.convert("1", dither=Image.Dither.NONE)
    return body.tobytes(), eye_mask.tobytes()


def rasterize_frame(frame: Image.Image, bbox: tuple[int, int, int, int], gif_name: str | None = None) -> bytes:
    mask, _ = rasterize_frame_masks(frame, bbox, gif_name)
    return mask


def emit_frame(name: str, index: int, data: bytes) -> list[str]:
    stride = (TARGET_SIZE + 7) // 8
    # I1 images need two lv_color32_t palette entries before bitmap bytes:
    # value 0 is transparent, value 1 is opaque black.
    palette = bytes([0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF])
    payload = palette + data
    return [
        f"static LV_ATTRIBUTE_MEM_ALIGN const uint8_t {name}_frame_{index}_map[] = {{",
        *("  " + line + "," for line in chunked(payload)),
        "};",
        f"static const lv_image_dsc_t {name}_frame_{index} = {{",
        "  .header = { .magic = LV_IMAGE_HEADER_MAGIC, .cf = LV_COLOR_FORMAT_I1,",
        f"               .flags = 0, .w = {TARGET_SIZE}, .h = {TARGET_SIZE}, .stride = {stride} }},",
        f"  .data_size = {len(payload)}, .data = {name}_frame_{index}_map,",
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
        # Prefer re-rendered gif from newgif/ over the upstream clawd-on-desk
        # source (see NEWGIF_DIR). Falls back to GIF_DIR for unchanged gifs.
        newgif_path = NEWGIF_DIR / gif_name
        gif_path = newgif_path if newgif_path.exists() else GIF_DIR / gif_name
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
            "bbox": union_bbox(frames, include_full_ink=gif_name in FULL_INK_BBOX_GIFS),
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
            body_data, eye_data = rasterize_frame_masks(frame, bbox, gif_name)
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
