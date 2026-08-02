"""Codex Radar intelligence efficiency data from codexradar.com.

Fetches the public fallback JSON snapshot and extracts IQ scores
for the model/effort combos displayed on the RLCD radar page.
"""
from __future__ import annotations

import os
import json
import time
import urllib.request

from schema import CodexRadar, RadarPoint, RadarTrend

TTL = int(os.environ.get("RLCD_CODEXRADAR_TTL", "600"))  # 10 min
DATA_URL = "https://codexradar.com/data/intelligence-efficiency.json?v=20260723-0710-history-metrics"

WANTED = {
    ("gpt-5.6-sol", "ultra"), ("gpt-5.6-sol", "max"), ("gpt-5.6-sol", "xhigh"),
    ("gpt-5.6-terra", "ultra"), ("gpt-5.6-terra", "max"), ("gpt-5.6-terra", "xhigh"),
    ("gpt-5.6-luna", "ultra"), ("gpt-5.6-luna", "max"), ("gpt-5.6-luna", "xhigh"),
    ("gpt-5.5", "xhigh"),
    ("deepseek-v4-flash", "max"),
}

WANTED_ORDERED = [
    ("gpt-5.6-sol", "ultra"), ("gpt-5.6-sol", "max"), ("gpt-5.6-sol", "xhigh"),
    ("gpt-5.6-terra", "ultra"), ("gpt-5.6-terra", "max"), ("gpt-5.6-terra", "xhigh"),
    ("gpt-5.6-luna", "ultra"), ("gpt-5.6-luna", "max"), ("gpt-5.6-luna", "xhigh"),
    ("gpt-5.5", "xhigh"),
    ("deepseek-v4-flash", "max"),
]

MAX_HISTORY = 12

MODEL_SHORT = {
    "gpt-5.6-sol": "sol", "gpt-5.6-terra": "terra", "gpt-5.6-luna": "luna",
    "gpt-5.5": "gpt-5.5", "deepseek-v4-flash": "deepseek",
}

_cache: dict[str, object] = {"d": None, "ts": 0.0}


def fetch_codexradar() -> CodexRadar | None:
    now = time.time()
    cached = _cache["d"]
    if cached is not None and now - float(_cache["ts"]) < TTL:
        return cached  # type: ignore
    try:
        req = urllib.request.Request(DATA_URL, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.load(r)
        points = []
        for p in d.get("points", []):
            model = p.get("model", "")
            effort = p.get("effort", "")
            if (model, effort) not in WANTED:
                continue
            points.append(RadarPoint(
                model=MODEL_SHORT.get(model, model),
                effort=effort,
                iq=p.get("iq"),
                price=p.get("average_price_usd"),
                minutes=p.get("average_minutes"),
                passed=p.get("passed", 0),
                tasks=p.get("valid_tasks", 112),
            ))
        cr = CodexRadar(
            updated_at=d.get("source_updated_at"),
            available=len(points) > 0,
            points=points,
        )
        # Build trends from history (last MAX_HISTORY snapshots)
        history = d.get("history", [])
        recent = history[-MAX_HISTORY:] if len(history) > MAX_HISTORY else history
        if recent:
            for model_key, effort in WANTED_ORDERED:
                short = MODEL_SHORT.get(model_key, model_key)
                iqs = []
                for snap in recent:
                    for p in snap.get("points", []):
                        if p.get("model") == model_key and p.get("effort") == effort:
                            iq = p.get("iq")
                            if iq is not None:
                                iqs.append(iq)
                            break
                if iqs:
                    cr.trends.append(RadarTrend(model=short, effort=effort, iqs=iqs))
        _cache.update(d=cr, ts=now)
        return cr
    except Exception:
        return cached  # type: ignore
