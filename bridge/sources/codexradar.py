"""Codex Radar intelligence efficiency data from codexradar.com.

Fetches the live metrics API the website renders for its 软件工程能力
(software-engineering) view and extracts IQ scores for the model/effort
combos displayed on the RLCD radar page. The static /data/ fallback
JSON carries older per-run numbers that no longer match the site.
"""
from __future__ import annotations

import os
import json
import time
import urllib.request

from schema import CodexRadar, RadarPoint, RadarTrend

TTL = int(os.environ.get("RLCD_CODEXRADAR_TTL", "180"))  # 3 min
# Site has three views: 综合智能 (composite) / 软件工程能力 (software) / 视觉空间推理
# (visual). The metrics API without params serves the composite view; the radar
# page tracks the software-engineering numbers, which is the default composite
# payload's underlying software data - same endpoint, bypass CDN cache with refresh=1.
DATA_URL = "https://codexradar.com/api/intelligence-efficiency-metrics?refresh=1"
FALLBACK_URL = "https://codexradar.com/data/intelligence-efficiency.json?v=20260723-0710-history-metrics"

WANTED = {
    ("gpt-6-astra", "ultra"), ("gpt-6-astra", "max"), ("gpt-6-astra", "xhigh"),
    ("gpt-6-astra", "high"), ("gpt-6-astra", "medium"), ("gpt-6-astra", "low"),
    ("gpt-6-sol", "ultra"), ("gpt-6-sol", "max"), ("gpt-6-sol", "xhigh"),
    ("gpt-6-sol", "high"), ("gpt-6-sol", "medium"), ("gpt-6-sol", "low"),
    ("gpt-6-terra", "ultra"), ("gpt-6-terra", "max"), ("gpt-6-terra", "xhigh"),
    ("gpt-6-terra", "high"), ("gpt-6-terra", "medium"), ("gpt-6-terra", "low"),
    ("gpt-5.6-terra", "ultra"), ("gpt-5.6-terra", "max"), ("gpt-5.6-terra", "xhigh"),
    ("gpt-5.6-terra", "high"), ("gpt-5.6-terra", "medium"), ("gpt-5.6-terra", "low"),
    ("gpt-6-luna", "max"), ("gpt-6-luna", "xhigh"),
    ("gpt-5.6-sol", "max"),
    ("deepseek-v4-flash", "max"),
}

WANTED_ORDERED = [
    ("gpt-6-astra", "ultra"), ("gpt-6-astra", "max"), ("gpt-6-astra", "xhigh"),
    ("gpt-6-astra", "high"), ("gpt-6-astra", "medium"), ("gpt-6-astra", "low"),
    ("gpt-6-sol", "ultra"), ("gpt-6-sol", "max"), ("gpt-6-sol", "xhigh"),
    ("gpt-6-sol", "high"), ("gpt-6-sol", "medium"), ("gpt-6-sol", "low"),
    ("gpt-6-terra", "ultra"), ("gpt-6-terra", "max"), ("gpt-6-terra", "xhigh"),
    ("gpt-6-terra", "high"), ("gpt-6-terra", "medium"), ("gpt-6-terra", "low"),
    ("gpt-6-luna", "max"), ("gpt-6-luna", "xhigh"),
    ("gpt-5.6-sol", "max"),
    ("deepseek-v4-flash", "max"),
]

# gpt-6-terra isn't live on codexradar yet (the site's gpt-6 series is
# astra/sol/luna). Until it appears the terra row serves gpt-5.6-terra
# numbers and switches automatically once the site adds gpt-6-terra.
TERRA_FALLBACK_MODEL = "gpt-5.6-terra"

MAX_HISTORY = 12

MODEL_SHORT = {
    "gpt-6-astra": "astra",
    "gpt-6-sol": "sol", "gpt-6-terra": "terra", "gpt-6-luna": "luna",
    "gpt-5.6-terra": "terra",  # terra-row fallback while gpt-6-terra is absent
    "gpt-5.6-sol": "sol5.6",   # legacy flagship, bottom-right corner cell
    "deepseek-v4-flash": "deepseek",
}

_cache: dict[str, object] = {"d": None, "ts": 0.0}


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _snap_iq(snap: dict, model: str, effort: str):
    for p in snap.get("points", []):
        if p.get("model") == model and p.get("effort") == effort:
            return p.get("iq")
    return None


def _extract(metrics: dict, history: list) -> CodexRadar:
    """Build the CodexRadar payload from the current-run metrics payload and
    the fallback JSON's history snapshots (metrics API has none)."""
    by_key = {}
    for p in metrics.get("points", []):
        key = (p.get("model", ""), p.get("effort", ""))
        if key in WANTED:
            by_key[key] = p

    def pick(model_key: str, effort: str):
        p = by_key.get((model_key, effort))
        if p is None and model_key == "gpt-6-terra":
            p = by_key.get((TERRA_FALLBACK_MODEL, effort))
        return p

    points = []
    for model_key, effort in WANTED_ORDERED:
        p = pick(model_key, effort)
        if p is None:
            continue
        model = p.get("model", "")
        tasks = p.get("total") or p.get("valid_tasks") or 336
        points.append(RadarPoint(
            model=MODEL_SHORT.get(model, model),
            effort=effort,
            iq=p.get("iq"),
            price=p.get("average_price_usd"),
            minutes=p.get("average_minutes"),
            passed=int(p.get("passed") or 0),
            tasks=int(tasks),
        ))
    cr = CodexRadar(
        updated_at=metrics.get("source_updated_at"),
        available=len(points) > 0,
        points=points,
    )
    # Sparklines show relative movement, so the fallback history's slight
    # lag is ok.
    recent = history[-MAX_HISTORY:]
    for model_key, effort in WANTED_ORDERED:
        short = MODEL_SHORT.get(model_key, model_key)
        iqs = []
        for snap in recent:
            iq = _snap_iq(snap, model_key, effort)
            if iq is None and model_key == "gpt-6-terra":
                iq = _snap_iq(snap, TERRA_FALLBACK_MODEL, effort)
            if iq is not None:
                iqs.append(iq)
        if iqs:
            cr.trends.append(RadarTrend(model=short, effort=effort, iqs=iqs))
    return cr


def fetch_codexradar() -> CodexRadar | None:
    now = time.time()
    cached = _cache["d"]
    if cached is not None and now - float(_cache["ts"]) < TTL:
        return cached  # type: ignore
    try:
        # Current IQ values come from the live metrics API - same source the
        # website renders. The static fallback JSON lags behind (older runs).
        try:
            metrics = _get_json(DATA_URL)
        except Exception:
            metrics = None
        if metrics:
            try:
                history_src = _get_json(FALLBACK_URL)
            except Exception:
                history_src = None
        else:
            # API down: serve the fallback JSON's (older) points and history.
            metrics = history_src = _get_json(FALLBACK_URL)
        cr = _extract(metrics, (history_src or {}).get("history", []))
        _cache.update(d=cr, ts=now)
        return cr
    except Exception:
        return cached  # type: ignore
