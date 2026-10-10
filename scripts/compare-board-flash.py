#!/usr/bin/env python3
"""Compare the firmware read back from the ESP32-S3-RLCD board against a local build.

Usage:
    python scripts/compare-board-flash.py [local.bin]
                                         [--board PATH] [--elf PATH] [--no-symbols]

Defaults (relative to the repo root):
    --board  board-backup/board-flash-2026-08-31.bin   (16MB flash dump or raw app image)
    local    firmware/build/rlcd_dashboard.bin
    --elf    firmware/build/rlcd_dashboard.elf

What it reports, in order:
    1. app descriptor (esp_app_desc_t) of both images - version, project name,
       build time, IDF version, ELF sha256
    2. the "UI build marker" string baked into the UI
    3. per-segment size / byte-diff / 64-byte window match rate
    4. human-readable string set diff (a shift-proof way to compare features)
    5. pointer-shift analysis of the .text segment: if nearly every differing
       4-byte word differs by a constant, the machine code is identical and only
       the rodata layout moved -> same source, different build inputs
    6. which symbols own the differing bytes (needs the ELF + xtensa nm)

Why this exists: firmware/sdkconfig, dependencies.lock and managed_components/
are all gitignored, so a rebuild on another machine silently picks up different
compiler settings and component versions. See
.workbuddy-ai/board-firmware-compare.md for the analysis this was written for.

Read the board back with (16MB takes ~24 min at the default 115200; the baud
rate is only applied when it is higher than the ROM default, so pass -b to
speed it up):
    X:\\ESP\\python_env\\idf5.5_py3.13_env\\Scripts\\esptool.exe --chip esp32s3 -p COM11 ^
        --before default_reset --after hard_reset read_flash 0 0x1000000 board-backup\\board-flash.bin
"""
from __future__ import annotations

import argparse
import bisect
import re
import struct
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_BOARD = ROOT / "board-backup" / "board-flash-2026-08-31.bin"
DEFAULT_LOCAL = ROOT / "firmware" / "build" / "rlcd_dashboard.bin"
DEFAULT_ELF = ROOT / "firmware" / "build" / "rlcd_dashboard.elf"

APP_MAGIC = 0xE9
PART_MAGIC = b"\xaa\x50"
PART_TABLE_OFFSET = 0x8000
APP_SUBTYPES = (0x00, 0x10, 0x20)  # factory / ota_0 / ota_1
APP_DESC_MAGIC = 0xABCD5432
IMAGE_HEADER_LEN = 0x18
SEGMENT_HEADER_LEN = 8
SHA256_LEN = 32

WINDOW = 64
WINDOW_SAMPLES = 200

# nm candidates, newest esp-* toolchain first
NM_GLOBS = (
    "tools/xtensa-esp-elf/*/xtensa-esp-elf/bin/xtensa-esp32s3-elf-nm.exe",
    "tools/xtensa-esp-elf/*/xtensa-esp-elf/bin/xtensa-esp32s3-elf-nm",
)

STR_RE = re.compile(rb"[\x20-\x7e]{6,}")


# --------------------------------------------------------------------------- #
# image parsing
# --------------------------------------------------------------------------- #
def extract_app(blob: bytes) -> tuple[bytes, str]:
    """Return the app image inside `blob` (a raw app image or a flash dump).

    A full flash dump also starts with 0xE9 (that is the bootloader image), so
    the partition table is probed first; only then is a raw image assumed.
    """
    table = blob[PART_TABLE_OFFSET:PART_TABLE_OFFSET + 0x1000]
    for i in range(0, len(table) - 32 + 1, 32):
        entry = table[i:i + 32]
        if entry[:2] != PART_MAGIC:
            continue
        ptype, subtype = entry[2], entry[3]
        offset, size = struct.unpack_from("<II", entry, 4)
        label = entry[12:28].split(b"\x00")[0].decode("ascii", "replace")
        if ptype != 0x00 or subtype not in APP_SUBTYPES or offset + 4 > len(blob):
            continue
        if blob[offset:offset + 1] == bytes([APP_MAGIC]):
            return blob[offset:offset + size], f"partition '{label}' @ {offset:#x} size {size:#x}"

    if blob[:1] == bytes([APP_MAGIC]):
        return blob, "raw app image"

    return blob, "unknown layout (no partition table, no 0xE9 magic)"


def segments(img: bytes) -> list[tuple[int, int, int]]:
    """Parse the app image header -> [(load_addr, size, file_offset), ...]."""
    if img[:1] != bytes([APP_MAGIC]):
        raise ValueError("not an ESP app image (missing 0xE9 magic)")
    off = IMAGE_HEADER_LEN
    out = []
    for _ in range(img[1]):
        load_addr, size = struct.unpack_from("<II", img, off)
        off += SEGMENT_HEADER_LEN
        out.append((load_addr, size, off))
        off += size
    return out


def image_length(img: bytes) -> int:
    """Length of the app image proper (segments + optional trailing sha256)."""
    _, size, off = segments(img)[-1]
    return off + size + (SHA256_LEN if img[0x17] else 0)


def app_desc(img: bytes) -> dict | None:
    i = img.find(struct.pack("<I", APP_DESC_MAGIC))
    if i < 0:
        return None
    d = img[i:i + 256]

    def s(off: int, n: int) -> str:
        return d[off:off + n].split(b"\x00")[0].decode("utf-8", "replace")

    return {
        "offset": i,
        "version": s(16, 32),
        "project_name": s(48, 32),
        "time": s(80, 16),
        "date": s(96, 16),
        "idf_ver": s(112, 32),
        "elf_sha256": d[144:176].hex(),
    }


def marker_string(img: bytes) -> str | None:
    i = img.find(b"UI build marker")
    if i < 0:
        return None
    return img[i:img.find(b"\x00", i)].decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# comparisons
# --------------------------------------------------------------------------- #
def is_text(s: bytes) -> bool:
    """Filter out binary noise that happens to look printable."""
    t = s.decode("ascii", "replace")
    letters = sum(c.isalpha() for c in t)
    return letters >= 6 and letters / len(t) >= 0.7 and re.search(r"[A-Za-z]{3,}", t) is not None


def text_strings(blob: bytes) -> set[bytes]:
    return {m.group() for m in STR_RE.finditer(blob) if is_text(m.group())}


def window_match(board: bytes, local: bytes, samples: int = WINDOW_SAMPLES) -> tuple[int, int]:
    """How many 64-byte windows of `local` appear ANYWHERE in `board`.

    A low ratio means the code genuinely differs; a high ratio means it is the
    same code laid out differently.
    """
    n = min(len(board), len(local))
    if n <= WINDOW:
        return 0, 0
    step = max(WINDOW, (n - WINDOW) // samples)
    total = found = 0
    for s in range(0, n - WINDOW, step):
        w = local[s:s + WINDOW]
        if w.count(w[:1]) == WINDOW:  # skip padding runs
            continue
        total += 1
        if board.find(w) >= 0:
            found += 1
    return found, total


def pointer_shift(board: bytes, local: bytes, load_addr: int) -> tuple[int, int, Counter]:
    """Are the differing bytes just 4-byte pointers shifted by a constant?

    Returns (differing_bytes, words_differing_by_a_constant, delta_histogram).
    """
    diffs = [i for i in range(len(board)) if board[i] != local[i]]
    words = {}
    for i in diffs:
        words.setdefault(i // 4 * 4, None)
    deltas: Counter = Counter()
    constant = 0
    for w in words:
        bw = struct.unpack_from("<I", board, w)[0]
        lw = struct.unpack_from("<I", local, w)[0]
        d = (bw - lw) & 0xFFFFFFFF
        deltas[d] += 1
    # a "constant shift" is any delta whose magnitude is small (a rodata shift),
    # in either direction
    for d, n in deltas.items():
        signed = d - 0x100000000 if d >= 0x80000000 else d
        if 0 < abs(signed) < 0x10000:
            constant += n
    return len(diffs), constant, deltas


def find_nm() -> Path | None:
    """Locate the xtensa nm shipped with the ESP-IDF toolchain."""
    for base in (Path("X:/ESP"), ROOT.parent / "ESP"):
        if not base.exists():
            continue
        for pat in NM_GLOBS:
            hits = sorted(base.glob(pat))
            if hits:
                return hits[-1]
    return None


def symbol_ranges(elf: Path, nm: Path) -> list[tuple[int, int, str]]:
    out = subprocess.run([str(nm), "--numeric-sort", "--print-size", str(elf)],
                         capture_output=True, text=True).stdout
    syms = []
    for line in out.splitlines():
        p = line.split()
        if len(p) < 4:
            continue
        try:
            a, s = int(p[0], 16), int(p[1], 16)
        except ValueError:
            continue
        if s:
            syms.append((a, a + s, p[3]))
    syms.sort()
    return syms


def symbol_owners(syms, base: int, board: bytes, local: bytes) -> Counter:
    starts = [s[0] for s in syms]
    owners: Counter = Counter()
    unowned = 0
    for i in range(min(len(board), len(local))):
        if board[i] == local[i]:
            continue
        va = base + i
        j = bisect.bisect_right(starts, va) - 1
        if j >= 0 and syms[j][0] <= va < syms[j][1]:
            owners[syms[j][2]] += 1
        else:
            unowned += 1
    owners["<no symbol / padding>"] = unowned
    return owners


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def hr(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("local", nargs="?", default=str(DEFAULT_LOCAL),
                    help="locally built app image (default: %(default)s)")
    ap.add_argument("--board", default=str(DEFAULT_BOARD), help="board dump (default: %(default)s)")
    ap.add_argument("--elf", default=str(DEFAULT_ELF), help="ELF for symbol attribution")
    ap.add_argument("--no-symbols", action="store_true", help="skip the symbol attribution section")
    args = ap.parse_args()

    board_file, local_file = Path(args.board), Path(args.local)
    for f in (board_file, local_file):
        if not f.exists():
            print(f"missing file: {f}", file=sys.stderr)
            return 1

    board, how = extract_app(board_file.read_bytes())
    blen = image_length(board)
    board = board[:blen]
    local = local_file.read_bytes()
    llen = image_length(local)
    local = local[:llen]

    print(f"board : {board_file}")
    print(f"        {how}, app image {blen} bytes ({blen / 1048576:.3f} MB)")
    print(f"local : {local_file}")
    print(f"        app image {llen} bytes ({llen / 1048576:.3f} MB)")
    print(f"delta : {llen - blen:+d} bytes ({(llen - blen) / 1024:+.1f} KB)")

    hr("1. app descriptor (build time / IDF version live here)")
    for label, img in (("board", board), ("local", local)):
        d = app_desc(img)
        if not d:
            print(f"[{label}] esp_app_desc magic not found")
            continue
        print(f"[{label}]")
        for k in ("version", "project_name", "date", "time", "idf_ver", "elf_sha256"):
            print(f"    {k:13} = {d[k]}")

    hr("2. UI build marker")
    bm, lm = marker_string(board), marker_string(local)
    print(f"[board] {bm}")
    print(f"[local] {lm}")
    print(f"identical: {bm == lm}")

    hr("3. segments")
    bs, ls = segments(board), segments(local)
    hdr = f"{'seg':>3} {'load':>11} {'board':>10} {'local':>10} {'delta':>9} {'same%':>8} {'win64':>14}"
    print(hdr)
    print("-" * len(hdr))
    for i in range(max(len(bs), len(ls))):
        if i >= len(bs) or i >= len(ls):
            print(f"{i:>3}  segment count differs: board={len(bs)} local={len(ls)}")
            break
        ba, bsz, boff = bs[i]
        la, lsz, loff = ls[i]
        B, L = board[boff:boff + bsz], local[loff:loff + lsz]
        m = min(len(B), len(L))
        same = sum(1 for x, y in zip(B[:m], L[:m]) if x == y)
        found, total = window_match(B, L)
        flag = "" if ba == la else f"  LOAD {la:#x}"
        print(f"{i:>3} {ba:#11x} {bsz:>10} {lsz:>10} {lsz - bsz:>+9} "
              f"{same * 100.0 / max(m, 1):7.2f}% {f'{found}/{total}':>14}{flag}")

    hr("4. human-readable strings")
    bset, lset = text_strings(board), text_strings(local)
    print(f"board={len(bset)}  local={len(lset)}  common={len(bset & lset)}  "
          f"only_board={len(bset - lset)}  only_local={len(lset - bset)}")
    for label, s in (("only on board", bset - lset), ("only in local", lset - bset)):
        if s:
            print(f"  {label}: {[x.decode('ascii', 'replace') for x in sorted(s)][:10]}")

    hr("5. are the .text differences just shifted rodata pointers?")
    seg = next((i for i, (a, s, o) in enumerate(bs) if a == 0x42000020), None)
    if seg is None or seg >= len(ls):
        print("no flash .text segment found - skipped")
    else:
        ba, bsz, boff = bs[seg]
        _, lsz, loff = ls[seg]
        m = min(bsz, lsz)
        B, L = board[boff:boff + m], local[loff:loff + m]
        ndiff, constant, deltas = pointer_shift(B, L, ba)
        print(f"segment {seg} @ {ba:#x}: {ndiff} differing bytes ({ndiff * 100.0 / m:.2f}%)")
        if ndiff:
            print(f"  4-byte words differing by a small constant: {constant}")
            print(f"  top deltas: {[(hex(d), n) for d, n in deltas.most_common(5)]}")
            if constant >= 0.9 * sum(deltas.values()):
                print("  => machine code is identical; only the rodata layout moved")
            else:
                print("  => the code itself differs (not just shifted addresses)")

    if not args.no_symbols:
        hr("6. which symbols own the differing bytes")
        nm = find_nm()
        elf = Path(args.elf)
        if seg is None or seg >= len(ls):
            print("no flash .text segment found - skipped")
        elif nm is None:
            print("xtensa nm not found - skipped")
        elif not elf.exists():
            print(f"{elf} not found - build first, or pass --elf")
        else:
            syms = symbol_ranges(elf, nm)
            ba, bsz, boff = bs[seg]
            _, lsz, loff = ls[seg]
            owners = symbol_owners(syms, ba, board[boff:boff + bsz], local[loff:loff + lsz])
            for name, n in owners.most_common(15):
                print(f"  {n:>7}  {name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
