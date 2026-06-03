"""Read real Claude Pro/Max window utilization.

Anthropic has no clean usage endpoint; the numbers Claude Code's /usage shows
come from `anthropic-ratelimit-unified-*` response headers on an authenticated
call. That call needs the OAuth token in ~/.claude/.credentials.json, which is
root-owned here. So a root systemd timer (scripts/rlcd-claude-limits.py) makes
the call and writes the parsed values to LIMITS_FILE; this module just reads it.
"""
from __future__ import annotations

import os
import json
import time
from datetime import datetime, timezone

from schema import ClaudeLimits

LIMITS_FILE = os.environ.get("RLCD_LIMITS_FILE", "/run/rlcd/claude-limits.json")
STALE_AFTER = int(os.environ.get("RLCD_LIMITS_STALE", "600"))  # 10 min


def fetch_limits() -> ClaudeLimits | None:
    try:
        with open(LIMITS_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    u5 = d.get("util_5h")
    u7 = d.get("util_7d")
    file_status = str(d.get("status", "unavailable"))
    if u5 is None or u5 < 0:
        return ClaudeLimits(status=file_status)
    age = time.time() - float(d.get("ts", 0))
    if age >= STALE_AFTER:
        status = "stale"
    elif file_status == "ok":
        status = "ok"
    else:
        status = file_status

    def ts(key: str) -> datetime | None:
        v = d.get(key)
        return datetime.fromtimestamp(int(v), tz=timezone.utc) if v else None

    def mins(key: str) -> int | None:
        v = d.get(key)
        return max(0, int((int(v) - time.time()) / 60)) if v else None

    return ClaudeLimits(
        util_5h=float(u5),
        util_7d=float(u7) if u7 is not None else None,
        reset_5h=ts("reset_5h"),
        reset_7d=ts("reset_7d"),
        reset_5h_min=mins("reset_5h"),
        reset_7d_min=mins("reset_7d"),
        status=status,
    )
