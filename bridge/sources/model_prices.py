"""Shared per-model token pricing for the usage aggregators.

One table covers every provider the aggregators see. Each entry stores prices
in its provider's NATIVE published currency ("currency": "CNY" or "USD"):

- DeepSeek platform models bill time-based peak/off-peak in CNY: peak hours
  are Beijing workdays 9:00-12:00 & 14:00-18:00 (= 01:00-04:00 & 06:00-10:00
  UTC), off-peak is half of peak (api-docs.deepseek.com zh-cn pricing).
  Entries therefore carry "peak" and "off_peak" rate dicts.
- Zhipu bigmodel GLM-5.3-Flash bills flat CNY with a launch 50% promotion
  running to the 2026-09-09 24:00 UTC+8 end that Z.ai documents for its USD
  rates ("promo" rate dict + "promo_until" UTC ISO timestamp). While active
  the promo takes precedence; afterwards the list rates apply.
- GLM-5.3 flagship and Meta Muse Spark are only officially published in USD
  and stay USD here.

All rate dicts are per 1M tokens: {"input", "cache_read", "output"}. Keys
match model names by case-insensitive substring, longest match wins
("glm-5.3-flash" beats "glm-5.3"); MODEL_ALIASES maps internal/stealth ids to
a canonical model first (exact id match). Models with no official price are
simply absent and cost 0.

Model ids come from different providers with different token semantics, so
callers pass already-split components: cache-miss input, cache-hit input and
output tokens (zcode's input_tokens includes cache reads and must be split by
the caller; dsh's usage object reports them separately). model_cost returns
the NATIVE amount + currency; panels aggregate one display currency, so
callers run the result through to_cny() when needed.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime

LOG = logging.getLogger(__name__)

RATE_KEYS = ("input", "cache_read", "output")

# Aliases: internal/stealth model ids that are actually another priced model.
# Exact id match (lowercased) before substring matching.
MODEL_ALIASES = {
    "ox-alpha": "glm-5.3-flash",
}

# Fixed approximate conversion used when a panel aggregates entries across
# currencies (e.g. Meta muse-spark usage inside the CNY dsh/zcode panels).
CNY_PER_USD = 7.2

DEFAULT_PRICES: dict[str, dict] = {
    # DeepSeek platform, CNY, peak / off-peak (off-peak is half of peak).
    "v4-flash": {"currency": "CNY",
                 "peak": {"input": 3.0, "cache_read": 0.10, "output": 9.0},
                 "off_peak": {"input": 1.5, "cache_read": 0.05, "output": 4.5}},
    "v4-pro": {"currency": "CNY",
               "peak": {"input": 9.0, "cache_read": 0.30, "output": 27.0},
               "off_peak": {"input": 4.5, "cache_read": 0.15, "output": 13.5}},
    # Zhipu bigmodel domestic CNY list prices; launch 50% promotion ending
    # 2026-09-09 24:00 UTC+8 (same window Z.ai documents for its USD rates).
    "glm-5.3-flash": {"currency": "CNY",
                      "input": 0.8, "cache_read": 0.23, "output": 2.8,
                      "promo": {"input": 0.4, "cache_read": 0.115, "output": 1.4},
                      "promo_until": "2026-09-09T16:00:00+00:00"},
    # Z.ai publishes the flagship in USD only — kept in USD, converted on
    # aggregation.
    "glm-5.3": {"currency": "USD", "input": 1.4, "cache_read": 0.26, "output": 4.4},
    # Tencent Hunyuan Hy3 CNY list (base <16K tier; the pricier long-context
    # tiers cannot be attributed from the usage rows).
    "hy3": {"currency": "CNY", "input": 1.0, "cache_read": 0.25, "output": 4.0},
    # Meta Muse Spark 1.2 contributor tier, USD native (standard tier lists
    # $1.25/$4.25; no published cache discount, so cache reads bill at the
    # input rate). Keyed to the exact variant id so a future standard-tier
    # muse-spark id does not inherit these rates.
    "muse-spark-1.2-contributor": {"currency": "USD", "input": 0.10, "cache_read": 0.10, "output": 0.20},
    # Alibaba Qwen3.8-Flash domestic CNY list.
    "qwen3.8-flash": {"currency": "CNY", "input": 0.8, "cache_read": 0.1, "output": 2.7},
}


def is_peak_time(dt_utc: datetime) -> bool:
    # DeepSeek peak hours: 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri
    # (= Beijing workdays 9:00-12:00 & 14:00-18:00).
    if dt_utc.weekday() >= 5:
        return False
    return 1 <= dt_utc.hour < 4 or 6 <= dt_utc.hour < 10


def _rate_set(entry: dict, key: str) -> dict:
    raw = entry.get(key) or {}
    return {k: float(raw.get(k, 0) or 0) for k in RATE_KEYS}


def _normalize_entry(entry: object) -> dict | None:
    """Canonicalize an entry to peak/off_peak rates plus optional promo.

    Accepts a flat {"input": .., "cache_read": .., "output": ..} that applies
    to both periods and/or {"peak": .., "off_peak": ..} splits, plus an
    optional {"promo": .., "promo_until": ISO-8601} promotion. "currency" is
    optional and defaults to CNY (the display currency of the domestic
    panels); recognized values are "CNY" and "USD".
    """
    if not isinstance(entry, dict):
        return None
    currency = str(entry.get("currency", "CNY")).upper()
    if currency not in ("CNY", "USD"):
        return None
    try:
        if "peak" in entry or "off_peak" in entry or "offpeak" in entry:
            peak = _rate_set(entry, "peak")
            off = _rate_set(entry, "off_peak") or _rate_set(entry, "offpeak") or peak
            normalized = {"peak": peak, "off_peak": off}
        else:
            flat = {k: float(entry.get(k, 0) or 0) for k in RATE_KEYS}
            normalized = {"peak": dict(flat), "off_peak": dict(flat)}
        if "promo" in entry and isinstance(entry["promo"], dict):
            normalized["promo"] = _rate_set(entry, "promo")
            normalized["promo_until"] = entry.get("promo_until")
        normalized["currency"] = currency
        return normalized
    except (TypeError, ValueError):
        return None


def load_prices(env_var: str) -> dict[str, dict]:
    """Default table merged with env overrides (same shapes as the defaults)."""
    table: dict[str, dict] = {}
    for key, entry in DEFAULT_PRICES.items():
        normalized = _normalize_entry(entry)
        if normalized is not None:
            table[key] = normalized
    raw = os.environ.get(env_var, "").strip()
    if raw:
        try:
            for key, entry in json.loads(raw).items():
                normalized = _normalize_entry(entry)
                if normalized is not None:
                    table[str(key)] = normalized
                else:
                    LOG.warning("ignoring malformed %s entry for %s", env_var, key)
        except ValueError:
            LOG.warning("ignoring malformed %s", env_var)
    return table


def _rates_for(entry: dict, dt_utc: datetime) -> dict:
    promo = entry.get("promo")
    if promo:
        until = entry.get("promo_until")
        try:
            if until and dt_utc < datetime.fromisoformat(str(until)):
                return promo
        except ValueError:
            LOG.warning("bad promo_until %r; skipping promotion", until)
    return entry["peak"] if is_peak_time(dt_utc) else entry["off_peak"]


def model_cost(
    model: str,
    input_miss_tokens: int,
    cache_hit_tokens: int,
    output_tokens: int,
    dt_utc: datetime,
    prices: dict[str, dict],
) -> tuple[float, str]:
    """NATIVE-currency cost for one request, picked by the request's UTC time."""
    lowered = (model or "").lower()
    lowered = MODEL_ALIASES.get(lowered, lowered)
    best_key: str | None = None
    for key in prices:
        if key.lower() in lowered and (best_key is None or len(key) > len(best_key)):
            best_key = key
    if best_key is None:
        return 0.0, "CNY"
    price = _rates_for(prices[best_key], dt_utc)
    amount = (
        input_miss_tokens * price["input"]
        + cache_hit_tokens * price["cache_read"]
        + output_tokens * price["output"]
    ) / 1e6
    return amount, prices[best_key]["currency"]


def to_cny(amount: float, currency: str, cny_per_usd: float = CNY_PER_USD) -> float:
    """Convert a native-currency amount into the panels' display currency."""
    if currency == "CNY":
        return amount
    if currency == "USD":
        return amount * cny_per_usd
    raise ValueError(f"unsupported currency {currency!r}")
