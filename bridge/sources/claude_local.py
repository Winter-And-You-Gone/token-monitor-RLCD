"""Pull Claude usage from local ccusage subprocess.

ccusage parses Claude Code's ~/.claude/projects/**/*.jsonl session logs and
emits JSON aggregations. We shell out instead of re-implementing the parser
so we inherit upstream fixes for free.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from schema import ActiveBlock, Bucket, ClaudeUsage, ModelBreakdown, OtherAgentUsage


CCUSAGE_CMD = os.environ.get("CCUSAGE_CMD", "npx -y ccusage@latest")
# 5h window with claude only; daily/monthly include all agents by default
DEFAULT_TIMEOUT = 60

# Optional limit overrides (Anthropic doesn't publish plan limits programmatically)
WEEKLY_LIMIT_USD = float(os.environ.get("RLCD_WEEKLY_LIMIT_USD", "0")) or None
BLOCK_LIMIT_USD = float(os.environ.get("RLCD_BLOCK_LIMIT_USD", "0")) or None
DEFAULT_TOKEN_LIMIT = os.environ.get("RLCD_TOKEN_LIMIT", "100M")


def _configured_timezone_name() -> str | None:
    name = os.environ.get("RLCD_TZ") or os.environ.get("TZ") or "Asia/Hong_Kong"
    try:
        ZoneInfo(name)
    except Exception:
        return None
    return name


def _configured_timezone():
    name = _configured_timezone_name()
    if name:
        return ZoneInfo(name)
    return timezone(timedelta(hours=8), name="UTC+8")


LOCAL_TZ_NAME = _configured_timezone_name()
LOCAL_TZ = _configured_timezone()


def _parse_token_limit(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip().replace("_", "")
    if not text or text.lower() in ("0", "none", "off", "false"):
        return None
    suffix = text[-1].lower()
    mult = 1
    if suffix in ("k", "m", "b"):
        text = text[:-1]
        mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[suffix]
    try:
        limit = int(float(text) * mult)
    except ValueError:
        return None
    return limit if limit > 0 else None


BLOCK_LIMIT_TOKENS = _parse_token_limit(os.environ.get("RLCD_BLOCK_LIMIT_TOKENS", DEFAULT_TOKEN_LIMIT))
WEEKLY_LIMIT_TOKENS = _parse_token_limit(os.environ.get("RLCD_WEEKLY_LIMIT_TOKENS", DEFAULT_TOKEN_LIMIT))


def _ccusage_args(args: list[str]) -> list[str]:
    if (
        LOCAL_TZ_NAME
        and any(part in {"daily", "monthly"} for part in args)
        and "--timezone" not in args
        and "-z" not in args
    ):
        return args + ["--timezone", LOCAL_TZ_NAME]
    return args


def _run(args: list[str]) -> dict[str, Any]:
    base_cmd = shlex.split(CCUSAGE_CMD)
    resolved = shutil.which(base_cmd[0])
    if resolved:
        base_cmd[0] = resolved
    effective_args = _ccusage_args(args)
    cmd = base_cmd + effective_args
    env = os.environ.copy()
    env.setdefault("npm_config_cache", "/tmp/.npm-cache")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=DEFAULT_TIMEOUT,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ccusage failed ({' '.join(effective_args)}): {proc.stderr.strip()[:400]}"
        )
    return json.loads(proc.stdout)


def _bucket(tokens: int, cost: float, limit_usd: float | None = None,
            limit_tokens: int | None = None) -> Bucket:
    pct = None
    if limit_tokens and limit_tokens > 0:
        pct = tokens / limit_tokens
    elif limit_usd and limit_usd > 0:
        pct = cost / limit_usd
    return Bucket(
        tokens_used=int(tokens),
        cost_usd=round(float(cost), 4),
        tokens_limit=limit_tokens,
        percent_used=round(pct, 4) if pct is not None else None,
    )


def _parse_active_block(blocks_json: dict[str, Any]) -> ActiveBlock | None:
    for blk in blocks_json.get("blocks", []):
        if blk.get("isActive") and not blk.get("isGap"):
            start = datetime.fromisoformat(blk["startTime"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(blk["endTime"].replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            minutes_left = max(0, int((end - now).total_seconds() // 60))
            tokens = int(blk.get("totalTokens", 0))
            cost = float(blk.get("costUSD", 0.0))
            projection = blk.get("projection") or {}
            if BLOCK_LIMIT_TOKENS:
                pct = tokens / BLOCK_LIMIT_TOKENS
            elif BLOCK_LIMIT_USD:
                pct = cost / BLOCK_LIMIT_USD
            else:
                pct = None
            return ActiveBlock(
                started_at=start,
                ends_at=end,
                tokens_used=tokens,
                cost_usd=round(cost, 4),
                tokens_limit=BLOCK_LIMIT_TOKENS,
                percent_used=round(pct, 4) if pct is not None else None,
                minutes_remaining=minutes_left,
                projection_tokens=projection.get("totalTokens"),
                projection_cost_usd=projection.get("totalCost"),
            )
    return None


def _period_of(e: dict[str, Any]) -> str:
    # Field name differs by command:
    #   unified `ccusage daily`         -> period (YYYY-MM-DD)
    #   agent `ccusage <agent> daily`   -> date   (YYYY-MM-DD)
    #   agent `ccusage <agent> monthly` -> month  (YYYY-MM)
    return e.get("period") or e.get("date") or e.get("month") or ""


def _sum_period(entries: list[dict[str, Any]]) -> tuple[int, float]:
    tokens = sum(int(e.get("totalTokens", 0)) for e in entries)
    cost = round(sum(_cost_of(e) for e in entries), 4)
    return tokens, cost


def _utc_period_fallback(now_local: datetime, fmt: str) -> str | None:
    """Return UTC period when local date/month is ahead of UTC grouping."""
    local_period = now_local.strftime(fmt)
    utc_period = now_local.astimezone(timezone.utc).strftime(fmt)
    return utc_period if utc_period != local_period else None


def _entries_for_period(
    entries: list[dict[str, Any]],
    period: str,
    fallback_period: str | None = None,
) -> list[dict[str, Any]]:
    matched = [e for e in entries if _period_of(e) == period]
    if matched or not fallback_period:
        return matched
    return [e for e in entries if _period_of(e) == fallback_period]


def _cost_of(e: dict[str, Any]) -> float:
    # ccusage command families disagree on the cost key:
    #   claude/unified entries -> totalCost
    #   agent entries          -> costUSD
    # The value is already computed from the model's official input/cache/output
    # token pricing, so keep raw token counts and use this cost as-is.
    for key in ("totalCost", "costUSD", "cost_usd"):
        if key in e and e[key] is not None:
            return float(e[key])
    return 0.0


def _aggregate_model_breakdown(daily_entries: list[dict[str, Any]], top_n: int = 5) -> list[ModelBreakdown]:
    agg: dict[str, dict[str, float]] = {}
    for e in daily_entries:
        for mb in e.get("modelBreakdowns", []) or []:
            name = mb.get("modelName") or "unknown"
            d = agg.setdefault(name, {"tokens": 0, "cost": 0.0})
            d["tokens"] += _model_token_total(mb)
            d["cost"] += float(mb.get("cost", 0.0))
        for name, mb in (e.get("models") or {}).items():
            d = agg.setdefault(name or "unknown", {"tokens": 0, "cost": 0.0})
            d["tokens"] += _model_token_total(mb)
            d["cost"] += _cost_of(mb)
    rows = [
        ModelBreakdown(model=k, tokens=int(v["tokens"]), cost_usd=round(v["cost"], 4))
        for k, v in agg.items()
    ]
    rows.sort(key=lambda r: r.tokens, reverse=True)
    return rows[:top_n]


def _model_token_total(m: dict[str, Any]) -> int:
    if "totalTokens" in m and m["totalTokens"] is not None:
        return int(m["totalTokens"])
    return sum(
        int(m.get(k, 0))
        for k in (
            "inputTokens",
            "outputTokens",
            "cacheCreationTokens",
            "cacheReadTokens",
            "cachedInputTokens",
            "reasoningOutputTokens",
        )
    )


def _model_tokens_today(
    daily_entries: list[dict[str, Any]],
    substr: str,
    today_str: str,
    fallback_today_str: str | None = None,
) -> int:
    """Sum tokens for models whose name contains `substr` in today's entry."""
    needle = substr.lower()
    total = 0
    for e in _entries_for_period(daily_entries, today_str, fallback_today_str):
        for mb in e.get("modelBreakdowns", []) or []:
            if needle in (mb.get("modelName") or "").lower():
                total += _model_token_total(mb)
        for name, mb in (e.get("models") or {}).items():
            if needle in (name or "").lower():
                total += _model_token_total(mb)
    return total


def fetch_claude() -> tuple[ClaudeUsage, int]:
    """Build a ClaudeUsage by spawning ccusage three times.

    Returns (claude_usage, deepseek_today_tokens) — the second value is the
    DeepSeek-model token count for today, extracted from the same ccusage data.
    """
    blocks_json = _run(["blocks", "--active", "--json"])
    daily_full = _run(["claude", "daily", "--json"])
    monthly_full = _run(["claude", "monthly", "--json"])

    daily_entries = daily_full.get("daily", []) or []
    monthly_entries = monthly_full.get("monthly", []) or []

    # Today: daily entry whose period == today (YYYY-MM-DD)
    now_local = datetime.now(LOCAL_TZ)
    today_str = now_local.strftime("%Y-%m-%d")
    today_fallback = _utc_period_fallback(now_local, "%Y-%m-%d")
    today_entries = _entries_for_period(daily_entries, today_str, today_fallback)
    today_tok, today_cost = _sum_period(today_entries)

    # Weekly: last 7 calendar days
    week_cutoff = (now_local - timedelta(days=6)).strftime("%Y-%m-%d")
    week_entries = [e for e in daily_entries if _period_of(e) >= week_cutoff]
    week_tok, week_cost = _sum_period(week_entries)

    # Month: current month
    month_str = now_local.strftime("%Y-%m")
    month_fallback = _utc_period_fallback(now_local, "%Y-%m")
    month_entries = _entries_for_period(monthly_entries, month_str, month_fallback)
    month_tok, month_cost = _sum_period(month_entries)

    # Lifetime: sum of all daily entries (could also use totals field)
    life_tok, life_cost = _sum_period(daily_entries)

    usage = ClaudeUsage(
        active_block=_parse_active_block(blocks_json),
        weekly=_bucket(week_tok, week_cost, WEEKLY_LIMIT_USD, WEEKLY_LIMIT_TOKENS),
        today=_bucket(today_tok, today_cost),
        month=_bucket(month_tok, month_cost),
        lifetime=_bucket(life_tok, life_cost),
        by_model=_aggregate_model_breakdown(daily_entries),
    )
    ds_today = _model_tokens_today(daily_entries, "deepseek", today_str, today_fallback)
    return usage, ds_today


def fetch_other_agents() -> list[OtherAgentUsage]:
    """Best-effort per-agent breakdown for codex/gemini/copilot using ccusage daily --instances.

    Returns [] silently if no other agents have any data.
    """
    out: list[OtherAgentUsage] = []
    for agent in ("codex", "gemini", "copilot"):
        try:
            daily = _run([agent, "daily", "--json"])
            monthly = _run([agent, "monthly", "--json"])
        except Exception:
            continue
        daily_entries = daily.get("daily", []) or []
        monthly_entries = monthly.get("monthly", []) or []
        if not daily_entries:
            continue
        now_local = datetime.now(LOCAL_TZ)
        today_str = now_local.strftime("%Y-%m-%d")
        month_str = now_local.strftime("%Y-%m")
        today_e = _entries_for_period(daily_entries, today_str, _utc_period_fallback(now_local, "%Y-%m-%d"))
        month_e = _entries_for_period(monthly_entries, month_str, _utc_period_fallback(now_local, "%Y-%m"))
        out.append(
            OtherAgentUsage(
                agent=agent,
                today=_bucket(*_sum_period(today_e)),
                month=_bucket(*_sum_period(month_e)),
                lifetime=_bucket(*_sum_period(daily_entries)),
                by_model=_aggregate_model_breakdown(daily_entries, top_n=3),
            )
        )
    return out
