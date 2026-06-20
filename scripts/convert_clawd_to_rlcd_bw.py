#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

import gen_pet_anim as pet


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = ROOT / "clawd-on-desk" / "assets" / "gif"
DEFAULT_OUTPUT_DIR = ROOT / "bridge" / "assets" / "clawd_rlcd"

BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)
TRANSPARENT = (255, 255, 255, 0)


def mapped_gif_names() -> set[str]:
    assets = set(pet.STATE_TO_ASSET.values()) | set(pet.EXTRA_ASSETS)
    return {pet.ASSET_TO_GIF[asset] for asset in assets if asset in pet.ASSET_TO_GIF}


def selected_gifs(input_dir: Path, mapped_only: bool) -> list[Path]:
    if mapped_only:
        names = mapped_gif_names()
        return sorted(input_dir / name for name in names if (input_dir / name).exists())
    return sorted(input_dir.glob("clawd-*.gif"))


def render_frame(frame: Image.Image, bbox: tuple[int, int, int, int], gif_name: str) -> tuple[Image.Image, Image.Image, Image.Image]:
    canvas = pet._frame_canvas(frame, bbox)
    black_mask, white_mask, ink_mask, _eyes = pet.rlcd_bw_mask_layers(canvas, gif_name)

    transparent = Image.new("RGBA", canvas.size, TRANSPARENT)
    transparent.paste(BLACK, mask=black_mask)
    transparent.paste(WHITE, mask=white_mask)

    preview = Image.new("RGBA", canvas.size, WHITE)
    preview.paste(BLACK, mask=black_mask)
    preview.paste(WHITE, mask=white_mask)

    mask_preview = Image.new("RGBA", canvas.size, WHITE)
    mask_preview.paste((0, 0, 0, 255), mask=ink_mask)
    mask_preview.paste((200, 200, 200, 255), mask=white_mask)
    return transparent, preview, mask_preview


def save_gif(path: Path, frames: list[Image.Image], durations: list[int]) -> None:
    if not frames:
        return
    rgb_frames = [frame.convert("RGB") for frame in frames]
    rgb_frames[0].save(
        path,
        save_all=True,
        append_images=rgb_frames[1:],
        duration=durations,
        loop=0,
        disposal=2,
    )


def make_contact_sheet(items: list[dict[str, object]], out_path: Path, size: int) -> None:
    if not items:
        return
    label_h = 26
    pad = 10
    cols = 4 if size <= 80 else 3
    cell_w = size + pad * 2
    cell_h = size + label_h + pad * 2
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGBA", (cell_w * cols, cell_h * rows), WHITE)
    draw = ImageDraw.Draw(sheet)

    for index, item in enumerate(items):
        x = (index % cols) * cell_w
        y = (index // cols) * cell_h
        image = item["preview"]
        assert isinstance(image, Image.Image)
        sheet.alpha_composite(image.convert("RGBA"), (x + pad, y + pad + label_h))
        label = str(item["name"])
        draw.text((x + pad, y + 6), label[:32], fill=(0, 0, 0, 255))

    sheet.save(out_path)


def convert_one_gif(gif_path: Path, out_dir: Path, size: int) -> dict[str, object]:
    pet.TARGET_SIZE = size
    frames, durations = pet.load_gif_frames(gif_path)
    bbox = pet.union_bbox(frames, include_full_ink=gif_path.name in pet.FULL_INK_BBOX_GIFS)

    stem = gif_path.stem
    frame_dir = out_dir / "frames" / stem
    preview_dir = out_dir / "preview_frames" / stem
    mask_dir = out_dir / "mask_preview_frames" / stem
    gifs_dir = out_dir / "gifs"
    gifs_dir.mkdir(parents=True, exist_ok=True)

    transparent_frames: list[Image.Image] = []
    preview_frames: list[Image.Image] = []
    mask_frames: list[Image.Image] = []
    for index, frame in enumerate(frames):
        transparent, preview, mask_preview = render_frame(frame, bbox, gif_path.name)
        transparent_frames.append(transparent)
        preview_frames.append(preview)
        mask_frames.append(mask_preview)

    save_gif(gifs_dir / f"{stem}.gif", preview_frames, durations)
    return {
        "source": str(gif_path),
        "name": gif_path.name,
        "size": size,
        "frame_count": len(frames),
        "duration_ms": sum(durations),
        "bbox": bbox,
        "preview": preview_frames[0] if preview_frames else Image.new("RGBA", (size, size), WHITE),
        "gif": str(gifs_dir / f"{stem}.gif"),
        "frames": str(frame_dir),
        "preview_frames": str(preview_dir),
        "mask_preview_frames": str(mask_dir),
        "_transparent_frames": transparent_frames,
        "_preview_frames": preview_frames,
        "_mask_frames": mask_frames,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert original Clawd color GIFs to RLCD-safe black/white assets."
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--size", type=int, action="append", default=None, help="Output square size. Repeatable.")
    parser.add_argument("--mapped-only", action="store_true", help="Only convert GIFs currently mapped into RLCD firmware.")
    parser.add_argument("--debug-frames", action="store_true", help="Also export per-frame transparent, preview, and mask PNGs.")
    args = parser.parse_args()

    sizes = args.size or [56, 184]
    gif_paths = selected_gifs(args.input_dir, args.mapped_only)
    if not gif_paths:
        raise SystemExit(f"no clawd GIFs found in {args.input_dir}")

    all_manifest: dict[str, object] = {
        "input_dir": str(args.input_dir),
        "mapped_only": args.mapped_only,
        "sizes": sizes,
        "outputs": {},
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for size in sizes:
        size_dir = args.output_dir / f"size-{size}"
        size_dir.mkdir(parents=True, exist_ok=True)
        items = [convert_one_gif(path, size_dir, size) for path in gif_paths]
        if args.debug_frames:
            for item in items:
                frame_dir = Path(str(item["frames"]))
                preview_dir = Path(str(item["preview_frames"]))
                mask_dir = Path(str(item["mask_preview_frames"]))
                frame_dir.mkdir(parents=True, exist_ok=True)
                preview_dir.mkdir(parents=True, exist_ok=True)
                mask_dir.mkdir(parents=True, exist_ok=True)
                stem = Path(str(item["name"])).stem
                for index, frame in enumerate(item["_transparent_frames"]):
                    assert isinstance(frame, Image.Image)
                    frame.save(frame_dir / f"{stem}_{index:03d}.png")
                for index, frame in enumerate(item["_preview_frames"]):
                    assert isinstance(frame, Image.Image)
                    frame.save(preview_dir / f"{stem}_{index:03d}.png")
                for index, frame in enumerate(item["_mask_frames"]):
                    assert isinstance(frame, Image.Image)
                    frame.save(mask_dir / f"{stem}_{index:03d}.png")
        make_contact_sheet(items, size_dir / "contact_sheet.png", size)
        manifest_items = []
        for item in items:
            entry = dict(item)
            entry.pop("preview", None)
            entry.pop("_transparent_frames", None)
            entry.pop("_preview_frames", None)
            entry.pop("_mask_frames", None)
            if not args.debug_frames:
                entry.pop("frames", None)
                entry.pop("preview_frames", None)
                entry.pop("mask_preview_frames", None)
            manifest_items.append(entry)
        all_manifest["outputs"][str(size)] = {
            "contact_sheet": str(size_dir / "contact_sheet.png"),
            "items": manifest_items,
        }

    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(all_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"converted {len(gif_paths)} GIFs for sizes {', '.join(str(s) for s in sizes)}")
    print(f"output: {args.output_dir}")
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
