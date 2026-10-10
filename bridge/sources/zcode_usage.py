"""ZCode token usage from the local zcode CLI database.

zcode records every model request attempt in ~/.zcode/cli/db/db.sqlite
(table model_usage): one row per attempt with provider/model ids, the epoch-ms
started_at and token counts. Unlike the Anthropic-style logs, input_tokens
already includes cache reads there — computed_total_tokens = input + output —
so the per-row total is just computed_total_tokens (falling back to
input+output when null). Requests are billed through subscription plans, not
per token, so cost stays 0.

The whole table is only a few thousand rows, so there is no file cache: the
aggregation re-queries SQLite on each fetch. The database is live (zcode
writes to it via WAL), hence the read-only URI and a busy timeout.

Costs come from sources/model_prices.py (official provider rates). ZCode's
input_tokens already includes cache reads — the opposite of the dsh logs —
so the caller splits cache-miss input as input_tokens - cache_read_input_tokens
before pricing. RLCD_ZCODE_PRICES extends/overrides the shared table for this
source only.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from schema import Bucket, ModelBreakdown, OtherAgentUsage

try:
    from sources.claude_local import LOCAL_TZ
    from sources.model_prices import load_prices, model_cost, to_cny
except ImportError:  # pragma: no cover - direct import layout (tests)
    from claude_local import LOCAL_TZ  # type: ignore
    from model_prices import load_prices, model_cost, to_cny  # type: ignore


LOG = logging.getLogger(__name__)

ZCODE_DB_PATH = Path(os.environ.get("RLCD_ZCODE_DB_PATH", "") or (Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"))


def _session_rows(db_path: Path | None = None, prices: dict | None = None) -> list[tuple]:
    """Query model_usage and yield (date, month, model, tokens, cost) rows."""
    if db_path is None:
        # Read the module attribute at call time so tests can point this at a
        # fixture database.
        db_path = ZCODE_DB_PATH
    if not db_path.is_file():
        return []
    if prices is None:
        prices = load_prices("RLCD_ZCODE_PRICES")
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=2.0)
    try:
        cur = con.execute(
            "SELECT started_at, model_id, computed_total_tokens, input_tokens, "
            "output_tokens, cache_read_input_tokens FROM model_usage"
        )
        rows: list[tuple] = []
        for started_ms, model, computed, inp, out, cache_read in cur:
            tokens = int(computed or 0) or (int(inp or 0) + int(out or 0))
            if tokens <= 0 or not started_ms:
                continue
            local = datetime.fromtimestamp(started_ms / 1000.0, LOCAL_TZ)
            utc = datetime.fromtimestamp(started_ms / 1000.0, timezone.utc)
            model = model or "unknown"
            cache_read = max(0, int(cache_read or 0))
            # input_tokens includes cache reads on this provider: split them
            # apart so the cache-hit rate is not billed at the miss rate.
            cache_miss = max(0, int(inp or 0) - cache_read)
            rows.append(
                (
                    local.strftime("%Y-%m-%d"),
                    local.strftime("%Y-%m"),
                    model,
                    tokens,
                    # Panels display CNY; foreign-currency entries (e.g. Meta
                    # muse-spark in USD) are converted at the fixed rate.
                    to_cny(*model_cost(model, cache_miss, cache_read, int(out or 0), utc, prices)),
                )
            )
        return rows
    except sqlite3.Error:
        LOG.warning("zcode usage query failed", exc_info=True)
        return []
    finally:
        con.close()


def _bucket(rows: list[tuple], want_date: str | None, want_month: str | None) -> Bucket:
    tokens = 0
    cost = 0.0
    for date, month, _model, tok, c in rows:
        if want_date is not None and date != want_date:
            continue
        if want_month is not None and month != want_month:
            continue
        tokens += tok
        cost += c
    return Bucket(tokens_used=tokens, cost=round(cost, 4))


def _aggregate(rows: list[tuple]) -> OtherAgentUsage | None:
    now = datetime.now(LOCAL_TZ)
    models: dict[str, dict[str, float]] = {}
    for _date, _month, model, tok, cost in rows:
        d = models.setdefault(model, {"tokens": 0, "cost": 0.0})
        d["tokens"] += tok
        d["cost"] += cost
    breakdown = sorted(
        (
            ModelBreakdown(model=name, tokens=int(v["tokens"]), cost=round(v["cost"], 4))
            for name, v in models.items()
        ),
        key=lambda r: r.tokens,
        reverse=True,
    )[:3]
    return OtherAgentUsage(
        agent="zcode",
        # Buckets aggregate CNY (native for the domestic models; foreign
        # entries like Meta muse-spark are converted via to_cny below).
        currency="CNY",
        today=_bucket(rows, now.strftime("%Y-%m-%d"), None),
        month=_bucket(rows, None, now.strftime("%Y-%m")),
        lifetime=_bucket(rows, None, None),
        by_model=breakdown,
    )


def fetch_zcode_usage() -> OtherAgentUsage | None:
    """Best-effort zcode usage; None when the database is absent/unreadable
    or holds no token data."""
    try:
        usage = _aggregate(_session_rows())
    except Exception:
        LOG.warning("zcode usage aggregation failed", exc_info=True)
        return None
    return usage if usage.lifetime.tokens_used > 0 else None
