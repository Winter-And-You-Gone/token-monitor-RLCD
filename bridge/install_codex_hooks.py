#!/usr/bin/env python3
"""Idempotently merge bridge/codex_hooks.json into ~/.codex/hooks.json.

codex CLI only reads the single global ``~/.codex/hooks.json`` (unlike zcode,
which discovers plugins via ``plugins.dirs``).  Other tools -- notably Clawd on
Desk -- also rewrite that file and clobber our ``pet_hook.js`` entries, which
breaks codex's trusted_hash and silently disables every hook.

This script keeps a project-owned source-of-truth (``bridge/codex_hooks.json``)
containing *only* our pet_hook.js entries, and merges it into the global file
while preserving every other hook consumer.  Re-run it after Clawd rewrites the
global file, then restart codex so it re-approves the hooks.

Usage (from anywhere)::

    python bridge/install_codex_hooks.py           # merge
    python bridge/install_codex_hooks.py --check   # dry-run, exit 1 if drift

"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "bridge" / "codex_hooks.json"
DST = Path.home() / ".codex" / "hooks.json"
MARKER = "pet_hook.js"


def _group_is_ours(group: object) -> bool:
    """True if a hook group contains a command referencing pet_hook.js."""
    if not isinstance(group, dict):
        return False
    hooks = group.get("hooks")
    if not isinstance(hooks, list):
        return False
    for h in hooks:
        if isinstance(h, dict) and MARKER in str(h.get("command", "")):
            return True
    return False


def _load(path: Path) -> dict:
    if not path.exists():
        return {"hooks": {}}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object")
    data.setdefault("hooks", {})
    return data


def merge(src: dict, dst: dict) -> tuple[dict, list[str]]:
    """Return (merged, changed_events).

    For each event in ``src``: drop existing pet_hook groups from the target
    then prepend ours, so ordering and content stay deterministic.
    """
    src_hooks = src.get("hooks", {})
    dst.setdefault("hooks", {})
    changed: list[str] = []
    for event, groups in src_hooks.items():
        if not isinstance(groups, list):
            continue
        existing = dst["hooks"].get(event, [])
        kept = [g for g in existing if not _group_is_ours(g)]
        desired = list(groups) + kept
        if existing != desired:
            changed.append(event)
        dst["hooks"][event] = desired
    return dst, changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--check", action="store_true", help="dry-run; exit 1 if global file would change")
    args = ap.parse_args()

    if not SRC.exists():
        print(f"[install_codex_hooks] source missing: {SRC}", file=sys.stderr)
        return 2
    src = _load(SRC)
    dst = _load(DST)

    merged, changed = merge(src, dst)

    if args.check:
        if changed:
            print(f"[install_codex_hooks] drift on events: {', '.join(changed)}")
            print(f"  global: {DST}")
            print(f"  source: {SRC}")
            return 1
        print("[install_codex_hooks] in sync")
        return 0

    if not changed:
        print(f"[install_codex_hooks] already in sync ({DST})")
        return 0

    if DST.exists():
        bak = DST.with_suffix(".json.bak")
        shutil.copy2(DST, bak)
        print(f"[install_codex_hooks] backup -> {bak}")

    DST.parent.mkdir(parents=True, exist_ok=True)
    with DST.open("w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"[install_codex_hooks] merged {len(changed)} events -> {DST}")
    print(f"  events: {', '.join(changed)}")
    print("[install_codex_hooks] restart codex and approve hooks to take effect")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
