"""Pull Claude usage from local ccusage subprocess.

ccusage parses Claude Code's ~/.claude/projects/**/*.jsonl session logs and
emits JSON aggregations. We shell out instead of re-implementing the parser
so we inherit upstream fixes for free.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from schema import ActiveBlock, Bucket, ClaudeUsage, ModelBreakdown, OtherAgentUsage


LOG = logging.getLogger(__name__)
PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, value)


# 5h window with claude only; daily/monthly include all agents by default
DEFAULT_TIMEOUT = _int_env("CCUSAGE_TIMEOUT_SEC", 20, minimum=1)
OTHER_TIMEOUT = _int_env("CCUSAGE_OTHER_TIMEOUT_SEC", 20, minimum=1)
BLOCKS_CACHE_TTL_SEC = _int_env("CCUSAGE_BLOCK_CACHE_TTL_SEC", 60, minimum=1)
PERIOD_CACHE_TTL_SEC = _int_env("CCUSAGE_PERIOD_CACHE_TTL_SEC", 300, minimum=1)
DEFAULT_CACHE_TTL_SEC = _int_env("CCUSAGE_CACHE_TTL_SEC", 60, minimum=1)
CCUSAGE_OFFLINE = os.environ.get("CCUSAGE_OFFLINE", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
OTHER_AGENTS = tuple(
    agent.strip().lower()
    for agent in os.environ.get("RLCD_OTHER_AGENTS", "codex").split(",")
    if agent.strip()
)

# Optional limit overrides (Anthropic doesn't publish plan limits programmatically)
WEEKLY_LIMIT_USD = float(os.environ.get("RLCD_WEEKLY_LIMIT_USD", "0")) or None
BLOCK_LIMIT_USD = float(os.environ.get("RLCD_BLOCK_LIMIT_USD", "0")) or None
DEFAULT_TOKEN_LIMIT = os.environ.get("RLCD_TOKEN_LIMIT", "100M")


def _configured_timezone_name() -> str:
    return os.environ.get("RLCD_TZ") or os.environ.get("TZ") or "Asia/Hong_Kong"


def _configured_timezone():
    name = os.environ.get("RLCD_TZ") or os.environ.get("TZ") or "Asia/Hong_Kong"
    try:
        return ZoneInfo(name)
    except Exception:
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
    out = list(args)
    if (
        LOCAL_TZ_NAME
        and any(part in {"daily", "monthly"} for part in out)
        and "--timezone" not in out
        and "-z" not in out
    ):
        out += ["--timezone", LOCAL_TZ_NAME]
    if CCUSAGE_OFFLINE and "--offline" not in out:
        out.append("--offline")
    return out


@dataclass
class _CcusageCacheEntry:
    value: dict[str, Any]
    ts: float


@dataclass
class _CcusageInflight:
    event: threading.Event
    result: dict[str, Any] | None = None
    error: BaseException | None = None


_ccusage_cache: dict[tuple[str, ...], _CcusageCacheEntry] = {}
_ccusage_inflight: dict[tuple[str, ...], _CcusageInflight] = {}
_ccusage_lock = threading.Lock()


def _split_configured_command(value: str) -> list[str]:
    return shlex.split(value, posix=(os.name != "nt"))


def _format_args(args: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in args)


def _command_label(command: list[str]) -> str:
    if not command:
        return "ccusage"
    return Path(command[0]).name or command[0]


def _reject_forbidden_command(command: list[str]) -> None:
    lowered = [part.lower() for part in command]
    if any("@latest" in part for part in lowered):
        raise RuntimeError(
            "Runtime ccusage command must not use @latest. "
            "Install a local command once with: npm install -g ccusage"
        )
    executable = Path(command[0]).name.lower() if command else ""
    if executable in {"npx", "npx.cmd", "npx.exe"}:
        raise RuntimeError(
            "Runtime ccusage command must not use npx. "
            "Install a local command once with: npm install -g ccusage"
        )


def _resolve_ccusage_command() -> list[str]:
    configured = os.environ.get("CCUSAGE_CMD", "").strip()
    if configured:
        command = _split_configured_command(configured)
        if not command:
            raise RuntimeError("CCUSAGE_CMD is empty")
        _reject_forbidden_command(command)
        resolved = shutil.which(command[0])
        if resolved:
            command[0] = resolved
        elif not Path(command[0]).exists():
            raise RuntimeError(
                "configured CCUSAGE_CMD was not found. "
                "Install a local command once with: npm install -g ccusage"
            )
        return command

    resolved = shutil.which("ccusage")
    if not resolved:
        raise RuntimeError(
            "ccusage command not found. Install it once with: npm install -g ccusage"
        )
    return [resolved]


def _subprocess_env_without_proxy() -> dict[str, str]:
    env = os.environ.copy()
    for name in PROXY_ENV_VARS:
        env.pop(name, None)
    return env


def _ccusage_cache_ttl(args: list[str]) -> int:
    if args[:3] == ["blocks", "--active", "--json"]:
        return BLOCKS_CACHE_TTL_SEC
    if any(part in {"daily", "monthly"} for part in args):
        return PERIOD_CACHE_TTL_SEC
    return DEFAULT_CACHE_TTL_SEC


def _ccusage_cache_key(args: list[str]) -> tuple[str, ...]:
    return (os.environ.get("CCUSAGE_CMD", "").strip(), *args)


def _run(args: list[str], timeout: int | None = None) -> dict[str, Any]:
    effective_args = _ccusage_args(args)
    timeout = DEFAULT_TIMEOUT if timeout is None else timeout
    ttl = _ccusage_cache_ttl(effective_args)
    cache_key = _ccusage_cache_key(effective_args)

    while True:
        with _ccusage_lock:
            now = time.monotonic()
            cached = _ccusage_cache.get(cache_key)
            if cached and now - cached.ts < ttl:
                LOG.info(
                    "ccusage cache hit args=%s age=%.1fs ttl=%ss",
                    _format_args(effective_args),
                    now - cached.ts,
                    ttl,
                )
                return cached.value

            inflight = _ccusage_inflight.get(cache_key)
            if inflight:
                if cached:
                    LOG.info(
                        "ccusage stale cache served while refresh is in-flight "
                        "args=%s age=%.1fs ttl=%ss",
                        _format_args(effective_args),
                        now - cached.ts,
                        ttl,
                    )
                    return cached.value
                LOG.info("ccusage waiting for in-flight query args=%s", _format_args(effective_args))
            else:
                inflight = _CcusageInflight(event=threading.Event())
                _ccusage_inflight[cache_key] = inflight
                LOG.info("ccusage cache miss args=%s ttl=%ss", _format_args(effective_args), ttl)
                break

        if not inflight.event.wait(timeout + 5):
            raise TimeoutError(f"timed out waiting for in-flight ccusage query: {_format_args(effective_args)}")
        if inflight.error:
            raise inflight.error
        if inflight.result is not None:
            return inflight.result

    started = time.monotonic()
    try:
        result = _execute_ccusage_uncached(effective_args, timeout)
    except BaseException as exc:
        elapsed = time.monotonic() - started
        LOG.warning(
            "ccusage query failed args=%s elapsed=%.2fs error=%s",
            _format_args(effective_args),
            elapsed,
            exc,
        )
        with _ccusage_lock:
            current = _ccusage_inflight.pop(cache_key, None)
            if current is inflight:
                inflight.error = exc
                inflight.event.set()
        raise

    elapsed = time.monotonic() - started
    with _ccusage_lock:
        _ccusage_cache[cache_key] = _CcusageCacheEntry(value=result, ts=time.monotonic())
        current = _ccusage_inflight.pop(cache_key, None)
        if current is inflight:
            inflight.result = result
            inflight.event.set()
    LOG.info("ccusage executed local command args=%s elapsed=%.2fs", _format_args(effective_args), elapsed)
    return result


def _execute_ccusage_uncached(effective_args: list[str], timeout: int) -> dict[str, Any]:
    base_cmd = _resolve_ccusage_command()
    cmd = base_cmd + effective_args
    env = _subprocess_env_without_proxy()
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    LOG.info(
        "ccusage starting local command=%s args=%s timeout=%ss",
        _command_label(base_cmd),
        _format_args(effective_args),
        timeout,
    )
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        creationflags=creationflags,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        raise subprocess.TimeoutExpired(
            cmd, timeout, output=stdout, stderr=stderr
        ) from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"ccusage failed ({' '.join(effective_args)}): {stderr.strip()[:400]}"
        )
    return json.loads(stdout)


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    proc.kill()


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


def fetch_other_agents(agents: tuple[str, ...] | None = None) -> list[OtherAgentUsage]:
    """Best-effort per-agent breakdown for Codex and optional peer agents.

    Returns [] silently if no other agents have any data.
    """
    out: list[OtherAgentUsage] = []
    for agent in agents if agents is not None else OTHER_AGENTS:
        try:
            daily = _run([agent, "daily", "--json"], timeout=OTHER_TIMEOUT)
            monthly = _run([agent, "monthly", "--json"], timeout=OTHER_TIMEOUT)
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
