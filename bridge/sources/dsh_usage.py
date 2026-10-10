"""Aggregate DeepSeek Harness (dsh) token usage from local session logs.

dsh persists every durable session event to
~/.dsh/sessions/<workspace>/session-<uuid>/session.jsonl.zstd — several
concatenated zstd frames (one per durable publish) whose frame headers omit
the content size, so decoding must loop decompressobj() on unused_data (same
quirk as bridge._dsh_decode). Token usage rides on the assistant/message
events:

    {"type": "assistant/message", "time": 1786630329478,
     "data": {"message": {"source": {"model": "deepseek-v4-flash", ...}},
              "usage": {"inputTokens": 54173, "outputTokens": 650,
                         "cacheReadTokens": 128, "reasoningTokens": 107}}}

reasoningTokens is a breakdown of outputTokens (provider completion_tokens
semantics), not additive, so totals are input + output + cacheRead (+ any
cache-write field the adapter reports). Unlike ccusage there is no cost data
in the logs, so cost is computed from a per-1M-token price table. DeepSeek
bills time-based peak/off-peak rates (peak = 01:00-04:00 and 06:00-10:00 UTC,
Mon-Fri; off-peak is half of peak), so each event picks its rate by its own
UTC timestamp. Entries accept either {"peak": {...}, "off_peak": {...}} or a
flat {"input": .., "cache_read": .., "output": ..} that applies to both
periods; env RLCD_DSH_PRICES (same shapes) merges over the defaults and
unknown models cost 0.

The full corpus is ~150MB, so per-file aggregations are cached by
(size, mtime_ns) and only changed files are re-decoded.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from schema import Bucket, ModelBreakdown, OtherAgentUsage

try:
    from sources.claude_local import LOCAL_TZ
    from sources.model_prices import load_prices, model_cost, to_cny
except ImportError:  # pragma: no cover - direct import layout (tests)
    from claude_local import LOCAL_TZ  # type: ignore
    from model_prices import load_prices, model_cost, to_cny  # type: ignore

try:
    import zstandard as _zstandard
except Exception:  # pragma: no cover - env without the extra
    _zstandard = None


LOG = logging.getLogger(__name__)

DSH_SESSION_DIR = Path(os.environ.get("RLCD_DSH_SESSION_DIR", "") or (Path.home() / ".dsh" / "sessions"))
TTL = int(os.environ.get("RLCD_DSH_USAGE_TTL", "300"))  # 5 min

# Official DeepSeek platform prices, USD per 1M tokens, peak / off-peak
# (api-docs.deepseek.com/quick_start/pricing). Keys match models by substring
# (case-insensitive, longest match wins); RLCD_DSH_PRICES (same shapes) merges
# over this.
_RATE_KEYS = ("input", "cache_read", "output")

DEFAULT_PRICES: dict[str, dict] = {
    "v4-flash": {
        "peak": {"input": 0.44, "cache_read": 0.014, "output": 1.32},
        "off_peak": {"input": 0.22, "cache_read": 0.007, "output": 0.66},
    },
    "v4-pro": {
        "peak": {"input": 1.32, "cache_read": 0.044, "output": 3.96},
        "off_peak": {"input": 0.66, "cache_read": 0.022, "output": 1.98},
    },
}


def _is_peak_time(dt_utc: datetime) -> bool:
    # DeepSeek peak hours: 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri.
    if dt_utc.weekday() >= 5:
        return False
    return 1 <= dt_utc.hour < 4 or 6 <= dt_utc.hour < 10


def _normalize_price_entry(entry: object) -> dict | None:
    """Accept {"peak": .., "off_peak": ..} or a flat rate valid for both."""
    if not isinstance(entry, dict):
        return None
    try:
        if "peak" in entry or "off_peak" in entry or "offpeak" in entry:
            peak = entry.get("peak") or {}
            off = entry.get("off_peak") or entry.get("offpeak") or {}
            return {
                "peak": {k: float(peak.get(k, 0) or 0) for k in _RATE_KEYS},
                "off_peak": {k: float(off.get(k, 0) or 0) for k in _RATE_KEYS},
            }
        flat = {k: float(entry.get(k, 0) or 0) for k in _RATE_KEYS}
    except (TypeError, ValueError):
        return None
    return {"peak": dict(flat), "off_peak": dict(flat)}


def _load_prices() -> dict[str, dict]:
    table: dict[str, dict] = {}
    for key, entry in DEFAULT_PRICES.items():
        normalized = _normalize_price_entry(entry)
        if normalized is not None:
            table[key] = normalized
    raw = os.environ.get("RLCD_DSH_PRICES", "").strip()
    if raw:
        try:
            for key, entry in json.loads(raw).items():
                normalized = _normalize_price_entry(entry)
                if normalized is not None:
                    table[str(key)] = normalized
                else:
                    LOG.warning("ignoring malformed RLCD_DSH_PRICES entry for %s", key)
        except ValueError:
            LOG.warning("ignoring malformed RLCD_DSH_PRICES")
    return table


def _model_cost(model: str, usage: dict, prices: dict[str, dict], dt_utc: datetime) -> float:
    lowered = (model or "").lower()
    best_key: str | None = None
    for key in prices:
        if key.lower() in lowered and (best_key is None or len(key) > len(best_key)):
            best_key = key
    if best_key is None:
        return 0.0
    period = "peak" if _is_peak_time(dt_utc) else "off_peak"
    price = prices[best_key][period]
    return (
        usage.get("inputTokens", 0) * price["input"]
        + usage.get("cacheReadTokens", 0) * price["cache_read"]
        + usage.get("outputTokens", 0) * price["output"]
    ) / 1e6


def _decode(file_path: Path) -> bytes | None:
    try:
        if not file_path.name.endswith(".zstd"):
            return file_path.read_bytes()
        if _zstandard is None:
            return None
        # Real dsh session files are several concatenated zstd frames (one per
        # durable publish) whose frame headers omit the content size. The
        # decompressobj()-loop fallback in bridge._dsh_decode is pathologically
        # slow on those, so stream across frames with the C-backed reader.
        with file_path.open("rb") as fh:
            return _zstandard.ZstdDecompressor().stream_reader(fh, read_across_frames=True).read()
    except Exception:
        return None


def _event_model(data: dict) -> str:
    message = data.get("message")
    if isinstance(message, dict):
        source = message.get("source")
        if isinstance(source, dict):
            model = source.get("model")
            if isinstance(model, str) and model:
                return model
    return "unknown"


def _event_tokens(usage: dict) -> int:
    return (
        int(usage.get("inputTokens", 0) or 0)
        + int(usage.get("outputTokens", 0) or 0)
        + int(usage.get("cacheReadTokens", 0) or 0)
        + int(usage.get("cacheCreationTokens", 0) or 0)
        + int(usage.get("cachedInputTokens", 0) or 0)
    )


def _session_files(root: Path | None = None) -> list[Path]:
    if root is None:
        # Read the module attribute at call time so tests can point the
        # scanner at a temp directory.
        root = DSH_SESSION_DIR
    if not root.is_dir():
        return []
    files: list[Path] = []
    try:
        files.extend(root.rglob("session.jsonl.zstd"))
        files.extend(root.rglob("session.jsonl"))
    except OSError:
        return []
    return sorted(set(files))


_MARKER = b'"assistant/message"'


def _rows_from_bytes(raw: bytes, fallback_ts: float, prices: dict[str, dict[str, float]]):
    """Yield (date, month, model, tokens, cost) rows for one session file.

    Decoded session text is multi-GB dominated by reasoning-chunk events, so
    instead of decoding it to str and splitting into lines, locate
    assistant/message lines directly in the bytes with memchr-speed find() and
    json-parse only those. A marker hit inside some other event's content
    string is still safe: the parsed type is verified below.
    """
    pos = 0
    total = len(raw)
    while True:
        idx = raw.find(_MARKER, pos)
        if idx < 0:
            return
        start = raw.rfind(b"\n", 0, idx) + 1
        end = raw.find(b"\n", idx)
        if end < 0:
            end = total
        pos = end + 1
        try:
            obj = json.loads(raw[start:end])
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict) or obj.get("type") != "assistant/message":
            continue
        data = obj.get("data")
        if not isinstance(data, dict):
            continue
        usage = data.get("usage")
        if not isinstance(usage, dict):
            continue
        ts_ms = obj.get("time")
        if isinstance(ts_ms, (int, float)) and ts_ms > 0:
            ts = ts_ms / 1000.0
        else:
            ts = fallback_ts
        local = datetime.fromtimestamp(ts, LOCAL_TZ)
        utc = datetime.fromtimestamp(ts, timezone.utc)
        model = _event_model(data)
        tokens = _event_tokens(usage)
        if tokens <= 0:
            continue
        yield (
            local.strftime("%Y-%m-%d"),
            local.strftime("%Y-%m"),
            model,
            tokens,
            # Panels display CNY; foreign-currency entries (e.g. Meta
            # muse-spark in USD) are converted at the fixed rate.
            to_cny(*model_cost(
                model,
                int(usage.get("inputTokens", 0) or 0),
                int(usage.get("cacheReadTokens", 0) or 0),
                int(usage.get("outputTokens", 0) or 0),
                utc,
                prices,
            )),
        )


class _FileRows:
    __slots__ = ("stat", "rows")

    def __init__(self, stat: tuple, rows: list[tuple]):
        self.stat = stat
        self.rows = rows


_file_cache: dict[Path, _FileRows] = {}
_usage_cache: dict[str, object] = {"d": None, "ts": 0.0, "busy": False}
# Serializes cache reads/writes and keeps a single refresh worker at a time.
_refresh_lock = threading.Lock()


def _collect_rows() -> list[tuple]:
    prices = load_prices("RLCD_DSH_PRICES")
    # Rows carry precomputed cost, so the per-file cache key must include the
    # active price table: an env change invalidates every cached file.
    price_fp = json.dumps(prices, sort_keys=True)
    files = _session_files()
    rows: list[tuple] = []
    for file_path in files:
        try:
            stat = file_path.stat()
            key = (stat.st_size, stat.st_mtime_ns, price_fp)
            fallback_ts = stat.st_mtime
        except OSError:
            continue
        cached = _file_cache.get(file_path)
        if cached is not None and cached.stat == key:
            rows.extend(cached.rows)
            continue
        raw = _decode(file_path)
        if raw is None:
            if cached is not None:
                rows.extend(cached.rows)
            continue
        file_rows = list(_rows_from_bytes(raw, fallback_ts, prices))
        _file_cache[file_path] = _FileRows(key, file_rows)
        rows.extend(file_rows)
    for stale in [p for p in _file_cache if p not in set(files)]:
        _file_cache.pop(stale, None)
    return rows


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
        agent="dsh",
        # Buckets aggregate CNY (native for the domestic models; foreign
        # entries like Meta muse-spark are converted via to_cny above).
        currency="CNY",
        today=_bucket(rows, now.strftime("%Y-%m-%d"), None),
        month=_bucket(rows, None, now.strftime("%Y-%m")),
        lifetime=_bucket(rows, None, None),
        by_model=breakdown,
    )


def _refresh_worker() -> None:
    try:
        usage = _aggregate(_collect_rows())
        with _refresh_lock:
            _usage_cache.update(d=usage, ts=time.time())
    except Exception:
        LOG.warning("dsh usage aggregation failed", exc_info=True)
    finally:
        with _refresh_lock:
            _usage_cache["busy"] = False


def fetch_dsh_usage() -> OtherAgentUsage | None:
    """Best-effort dsh usage; None until the first background scan lands.

    A full corpus scan takes tens of seconds, so a cold/stale cache never
    blocks the caller: the scan runs on a one-shot daemon thread and callers
    keep seeing the previous value (possibly None) until it finishes.
    """
    if _zstandard is None:
        return None
    now = time.time()
    with _refresh_lock:
        cached = _usage_cache["d"]
        result = cached if isinstance(cached, OtherAgentUsage) else None
        if result is not None and now - float(_usage_cache["ts"]) < TTL:
            return result
        if _usage_cache["busy"]:
            return result  # scan in flight; serve stale
        _usage_cache["busy"] = True
    threading.Thread(target=_refresh_worker, name="dsh-usage", daemon=True).start()
    return result
