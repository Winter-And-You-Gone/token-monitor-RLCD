#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import gen_pet_anim as base


ROOT = Path(__file__).resolve().parents[1]
OUT_C = ROOT / "firmware" / "components" / "ui_app" / "pet_big_anim.c"
OUT_H = ROOT / "firmware" / "components" / "ui_app" / "pet_big_anim.h"
TARGET_SIZE = 176

STATE_TO_ASSET = {
    **base.STATE_TO_ASSET,
    "completed": "clawd-happy.svg",
}
ASSET_TO_GIF = dict(base.ASSET_TO_GIF)
EXTRA_ASSETS = set(base.EXTRA_ASSETS)
IDLE_ASSET = STATE_TO_ASSET["idle"]


def needed_gifs() -> dict[str, Path]:
    assets = set(STATE_TO_ASSET.values()) | EXTRA_ASSETS
    missing_assets = sorted(asset for asset in assets if asset not in ASSET_TO_GIF)
    if missing_assets:
        raise SystemExit(f"missing GIF mapping for: {', '.join(missing_assets)}")
    result: dict[str, Path] = {}
    for asset in sorted(assets):
        gif_name = ASSET_TO_GIF[asset]
        gif_path = base.GIF_DIR / gif_name
        if not gif_path.exists():
            raise SystemExit(f"missing source GIF: {gif_path}")
        result[gif_name] = gif_path
    return result


def main() -> None:
    base.TARGET_SIZE = TARGET_SIZE

    gif_specs: dict[str, dict[str, object]] = {}
    for gif_name, gif_path in needed_gifs().items():
        frames, durations = base.load_gif_frames(gif_path)
        gif_specs[gif_name] = {
            "frames": frames,
            "durations": durations,
            "bbox": base.union_bbox(frames),
        }

    OUT_H.write_text(
        "\n".join(
            [
                "#pragma once",
                "",
                '#include "pet_anim.h"',
                "",
                "#ifdef __cplusplus",
                'extern "C" {',
                "#endif",
                "",
                "const pet_anim_sequence_t *ui_pet_big_anim_for_asset(const char *asset);",
                "const pet_anim_sequence_t *ui_pet_big_anim_for_state(const char *state);",
                "const pet_anim_sequence_t *ui_pet_big_anim_idle(void);",
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
        '#include "pet_big_anim.h"',
        "",
        "#include <stddef.h>",
        "#include <string.h>",
        "",
        "#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))",
        "",
    ]

    sequence_names: dict[str, str] = {}
    for gif_name, spec in gif_specs.items():
        sequence_name = "big_" + base.sanitize(Path(gif_name).stem)
        sequence_names[gif_name] = sequence_name
        frames = spec["frames"]
        durations = spec["durations"]
        bbox = spec["bbox"]
        c_lines.append(f"// {gif_name}")
        for index, frame in enumerate(frames):
            body_data, eye_data = base.rasterize_frame_masks(frame, bbox)
            c_lines.extend(base.emit_frame(sequence_name, index, body_data))
            c_lines.extend(base.emit_frame(f"{sequence_name}_eyes", index, eye_data))
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
    c_lines.append(f"static const pet_anim_sequence_t *const PET_BIG_ANIM_IDLE = &{idle_sequence_name}_sequence;")
    c_lines.append("")
    c_lines.append("typedef struct {")
    c_lines.append("    const char *asset;")
    c_lines.append("    const pet_anim_sequence_t *sequence;")
    c_lines.append("} pet_big_anim_asset_map_t;")
    c_lines.append("")
    c_lines.append("static const pet_big_anim_asset_map_t PET_BIG_ANIM_ASSET_MAP[] = {")
    for asset_name in sorted(set(STATE_TO_ASSET.values()) | EXTRA_ASSETS):
        gif_name = ASSET_TO_GIF[asset_name]
        c_lines.append(f'    {{"{asset_name}", &{sequence_names[gif_name]}_sequence}},')
    c_lines.append("};")
    c_lines.append("")
    c_lines.append("static const struct {")
    c_lines.append("    const char *state;")
    c_lines.append("    const char *asset;")
    c_lines.append("} PET_BIG_ANIM_STATE_MAP[] = {")
    for state, asset in STATE_TO_ASSET.items():
        c_lines.append(f'    {{"{state}", "{asset}"}},')
    c_lines.append("};")
    c_lines.append("")
    c_lines.append("static const pet_anim_sequence_t *lookup_asset(const char *asset)")
    c_lines.append("{")
    c_lines.append("    if (!asset || !asset[0]) return PET_BIG_ANIM_IDLE;")
    c_lines.append("    for (size_t i = 0; i < ARRAY_SIZE(PET_BIG_ANIM_ASSET_MAP); ++i) {")
    c_lines.append("        if (strcmp(PET_BIG_ANIM_ASSET_MAP[i].asset, asset) == 0) {")
    c_lines.append("            return PET_BIG_ANIM_ASSET_MAP[i].sequence;")
    c_lines.append("        }")
    c_lines.append("    }")
    c_lines.append("    return PET_BIG_ANIM_IDLE;")
    c_lines.append("}")
    c_lines.append("")
    c_lines.append("const pet_anim_sequence_t *ui_pet_big_anim_for_asset(const char *asset)")
    c_lines.append("{")
    c_lines.append("    return lookup_asset(asset);")
    c_lines.append("}")
    c_lines.append("")
    c_lines.append("const pet_anim_sequence_t *ui_pet_big_anim_idle(void)")
    c_lines.append("{")
    c_lines.append("    return PET_BIG_ANIM_IDLE;")
    c_lines.append("}")
    c_lines.append("")
    c_lines.append("const pet_anim_sequence_t *ui_pet_big_anim_for_state(const char *state)")
    c_lines.append("{")
    c_lines.append("    if (!state || !state[0]) return ui_pet_big_anim_idle();")
    c_lines.append("    for (size_t i = 0; i < ARRAY_SIZE(PET_BIG_ANIM_STATE_MAP); ++i) {")
    c_lines.append("        if (strcmp(PET_BIG_ANIM_STATE_MAP[i].state, state) == 0) {")
    c_lines.append("            return lookup_asset(PET_BIG_ANIM_STATE_MAP[i].asset);")
    c_lines.append("        }")
    c_lines.append("    }")
    c_lines.append("    return ui_pet_big_anim_idle();")
    c_lines.append("}")
    c_lines.append("")

    OUT_C.write_text("\n".join(c_lines), encoding="utf-8")


if __name__ == "__main__":
    main()
