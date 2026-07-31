"""Current weather — CMA first for China-friendly station data, Open-Meteo fallback.

Set CAIYUN_API_KEY to use Caiyun as a coordinate fallback; otherwise falls back
to Open-Meteo (no key needed). Caiyun URL format:
/v2.6/{token}/{lon},{lat}/realtime  (note: lon first)
"""
from __future__ import annotations

import os
import time
import json
import urllib.parse
import urllib.request

from schema import Weather

CAIYUN_KEY = os.environ.get("CAIYUN_API_KEY") or None
CMA_ENABLED = os.environ.get("RLCD_WEATHER_CMA", "1") != "0"
DEFAULT_LAT = float(os.environ.get("RLCD_WEATHER_LAT", "30.2741"))
DEFAULT_LON = float(os.environ.get("RLCD_WEATHER_LON", "120.1551"))
DEFAULT_CITY = os.environ.get("RLCD_WEATHER_CITY", "杭州")
TTL = int(os.environ.get("RLCD_WEATHER_TTL", "600"))

# Caiyun skycon -> (short label, icon key)
_SKYCON: dict[str, tuple[str, str]] = {
    "CLEAR_DAY":           ("Clear",  "clear"),
    "CLEAR_NIGHT":         ("Clear",  "clear"),
    "PARTLY_CLOUDY_DAY":   ("Partly", "partly"),
    "PARTLY_CLOUDY_NIGHT": ("Partly", "partly"),
    "CLOUDY":              ("Cloudy", "cloud"),
    "LIGHT_RAIN":          ("Rain",   "rain"),
    "MODERATE_RAIN":       ("Rain",   "rain"),
    "HEAVY_RAIN":          ("Heavy",  "rain"),
    "STORM_RAIN":          ("Storm",  "rain"),
    "FOG":                 ("Fog",    "fog"),
    "LIGHT_SNOW":          ("Snow",   "snow"),
    "MODERATE_SNOW":       ("Snow",   "snow"),
    "HEAVY_SNOW":          ("Snow",   "snow"),
    "STORM_SNOW":          ("Snow",   "snow"),
    "DUST":                ("Haze",   "fog"),
    "SAND":                ("Haze",   "fog"),
    "WIND":                ("Windy",  "cloud"),
}

# open-meteo WMO code -> (label, icon) — used only when CAIYUN_API_KEY is unset
_WMO: dict[int, tuple[str, str]] = {
    0: ("Clear", "clear"), 1: ("Clear", "clear"), 2: ("Partly", "partly"),
    3: ("Cloudy", "cloud"), 45: ("Fog", "fog"), 48: ("Fog", "fog"),
    51: ("Drizzle", "rain"), 53: ("Drizzle", "rain"), 55: ("Rain", "rain"),
    61: ("Rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy", "rain"),
    71: ("Snow", "snow"), 73: ("Snow", "snow"), 75: ("Snow", "snow"),
    80: ("Rain", "rain"), 81: ("Rain", "rain"), 82: ("Heavy", "rain"),
    85: ("Snow", "snow"), 86: ("Snow", "snow"),
    95: ("Storm", "rain"), 96: ("Storm", "rain"), 99: ("Storm", "rain"),
}

_cache: dict[tuple[object, ...], dict[str, object]] = {}
_geo_cache: dict[str, tuple[float, float, str]] = {}
_cma_station_cache: dict[str, tuple[str, str, str]] = {}
_CITY_ASCII_ALIASES = {
    "北京": "Beijing",
    "上海": "Shanghai",
    "广州": "Guangzhou",
    "深圳": "Shenzhen",
    "杭州": "Hangzhou",
    "成都": "Chengdu",
    "重庆": "Chongqing",
    "南京": "Nanjing",
    "苏州": "Suzhou",
    "武汉": "Wuhan",
    "西安": "Xian",
    "天津": "Tianjin",
    "郑州": "Zhengzhou",
    "长沙": "Changsha",
    "青岛": "Qingdao",
    "宁波": "Ningbo",
    "厦门": "Xiamen",
    "福州": "Fuzhou",
    "合肥": "Hefei",
    "济南": "Jinan",
    "沈阳": "Shenyang",
    "大连": "Dalian",
    "哈尔滨": "Harbin",
    "昆明": "Kunming",
    "贵阳": "Guiyang",
    "南宁": "Nanning",
    "海口": "Haikou",
    "三亚": "Sanya",
    "乌鲁木齐": "Urumqi",
    "呼和浩特": "Hohhot",
    "银川": "Yinchuan",
    "兰州": "Lanzhou",
    "西宁": "Xining",
    "太原": "Taiyuan",
    "石家庄": "Shijiazhuang",
    "长春": "Changchun",
    "南昌": "Nanchang",
    "无锡": "Wuxi",
    "常州": "Changzhou",
    "温州": "Wenzhou",
    "嘉兴": "Jiaxing",
    "绍兴": "Shaoxing",
}


def _ascii_name(value: str) -> str:
    return "".join(ch for ch in value if ord(ch) < 128).strip()


def _ascii_city_name(value: str) -> str:
    value = value.strip()
    return _ascii_name(value) or _CITY_ASCII_ALIASES.get(value, "")


def _request_json(url: str) -> dict[str, object]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://weather.cma.cn/",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _openmeteo_condition(code: int, cloud_cover: float, precip: float) -> tuple[str, str]:
    if code in (45, 48):
        return "Fog", "fog"
    if code in (71, 73, 75, 77, 85, 86):
        return "Snow", "snow"
    if precip >= 2.0:
        return "Heavy", "rain"
    if precip >= 0.3:
        return "Rain", "rain"
    if precip > 0:
        return "Drizzle", "rain"
    if cloud_cover < 20:
        return "Clear", "clear"
    if cloud_cover < 50:
        return "Partly", "partly"
    if cloud_cover < 85:
        return "Cloudy", "cloud"
    return "Overcast", "cloud"


def _condition_from_text(text: str) -> tuple[str, str]:
    value = text.strip()
    if not value:
        return "Cloudy", "cloud"
    if any(k in value for k in ("雷", "暴雨", "大暴雨", "特大暴雨")):
        return "Storm", "rain"
    if any(k in value for k in ("大雨", "中雨", "强阵雨")):
        return "Heavy", "rain"
    if any(k in value for k in ("雨", "阵雨", "毛毛雨")):
        return "Rain", "rain"
    if "雪" in value:
        return "Snow", "snow"
    if any(k in value for k in ("雾", "霾", "沙", "尘")):
        return "Fog", "fog"
    if "晴" in value and "云" not in value:
        return "Clear", "clear"
    if "多云" in value:
        return "Partly", "partly"
    if "阴" in value or "云" in value:
        return "Overcast", "cloud"
    return "Cloudy", "cloud"


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _fetch_caiyun(lat: float, lon: float, city: str, city_ascii: str = "") -> Weather | None:
    url = f"https://api.caiyunapp.com/v2.6/{CAIYUN_KEY}/{lon},{lat}/realtime"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            d = json.load(r)
        rt = d["result"]["realtime"]
        skycon = rt.get("skycon", "")
        label, icon = _SKYCON.get(skycon, ("Cloudy", "cloud"))
        return Weather(
            temp_c=round(float(rt["temperature"]), 1),
            code=0,
            condition=label,
            icon=icon,
            city=city,
            city_ascii=(city_ascii or _ascii_city_name(city))[:24],
        )
    except Exception:
        return None


def _fetch_openmeteo(lat: float, lon: float, city: str, city_ascii: str = "") -> Weather | None:
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,weather_code,cloud_cover,precipitation&timezone=auto"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            d = json.load(r)
        cur = d["current"]
        code   = int(cur["weather_code"])
        cloud  = float(cur.get("cloud_cover") or 0)
        precip = float(cur.get("precipitation") or 0)
        label, icon = _openmeteo_condition(code, cloud, precip)
        return Weather(
            temp_c=round(float(cur["temperature_2m"]), 1),
            code=code,
            condition=label,
            icon=icon,
            city=city,
            city_ascii=(city_ascii or _ascii_city_name(city))[:24],
        )
    except Exception:
        return None


def _cma_station_for_city(city: str) -> tuple[str, str, str] | None:
    query = city.strip()
    if not query:
        return None
    key = query.casefold()
    if key in _cma_station_cache:
        return _cma_station_cache[key]

    params = urllib.parse.urlencode({"q": query})
    url = f"https://weather.cma.cn/api/autocomplete?{params}"
    station = None
    try:
        data = _request_json(url)
        for item in data.get("data") or []:
            parts = str(item).split("|")
            if len(parts) >= 3 and parts[0] and parts[1]:
                ascii_city = _ascii_city_name(query) or _ascii_city_name(parts[2]) or _ascii_city_name(parts[1])
                station = (parts[0], parts[1], ascii_city)
                break
    except Exception:
        station = None
    if station is not None:
        _cma_station_cache[key] = station
    return station


def _fetch_cma(city: str) -> Weather | None:
    station = _cma_station_for_city(city)
    if station is None:
        return None
    station_id, station_city, station_city_ascii = station
    params = urllib.parse.urlencode({"stationid": station_id})
    url = f"https://weather.cma.cn/api/weather/view?{params}"
    try:
        data = _request_json(url)
        body = data["data"]  # type: ignore[index]
        now = body["now"]  # type: ignore[index]
        location = body.get("location") or {}  # type: ignore[union-attr]
        daily = body.get("daily") or []  # type: ignore[union-attr]
        hour = int(str(body.get("lastUpdate", "")).rsplit(" ", 1)[-1].split(":", 1)[0])
        use_night = hour < 6 or hour >= 18
        today = daily[0] if daily else {}
        text = str(today.get("nightText" if use_night else "dayText", ""))
        code = today.get("nightCode" if use_night else "dayCode")
        label, icon = _condition_from_text(text)
        local_city = str(location.get("name") or station_city or city)[:24]
        ascii_city = _ascii_city_name(station_city_ascii) or _ascii_city_name(city) or local_city
        return Weather(
            temp_c=round(float(now["temperature"]), 1),
            code=_int_or_none(code),
            condition=label,
            icon=icon,
            city=local_city,
            city_ascii=ascii_city[:24],
        )
    except Exception:
        return None


def _geocode_city(city: str) -> tuple[float, float, str] | None:
    query = city.strip()
    if not query:
        return None
    key = query.casefold()
    if key in _geo_cache:
        return _geo_cache[key]

    languages = ["en", "zh"]
    coords = None
    for language in languages:
        params = urllib.parse.urlencode({
            "name": query,
            "count": 3,
            "language": language,
            "format": "json",
        })
        url = f"https://geocoding-api.open-meteo.com/v1/search?{params}"
        try:
            data = _request_json(url)
            first = (data.get("results") or [])[0]
            name = str(first.get("name") or "")
            coords = (float(first["latitude"]), float(first["longitude"]), _ascii_city_name(name) or _ascii_city_name(query))
            break
        except Exception:
            continue
    if coords is not None:
        _geo_cache[key] = coords
    return coords


def _weather_config(
    lat: float | None = None,
    lon: float | None = None,
    city: str | None = None,
) -> tuple[float, float, str, str]:
    display_city = (city or DEFAULT_CITY).strip() or DEFAULT_CITY
    if lat is None and lon is None and city:
        coords = _geocode_city(display_city)
        if coords is not None:
            return coords[0], coords[1], display_city[:24], coords[2][:24]
    return (
        DEFAULT_LAT if lat is None else float(lat),
        DEFAULT_LON if lon is None else float(lon),
        display_city[:24],
        _ascii_city_name(display_city)[:24],
    )


def fetch_weather(
    lat: float | None = None,
    lon: float | None = None,
    city: str | None = None,
) -> Weather | None:
    display_city = (city or DEFAULT_CITY).strip() or DEFAULT_CITY
    explicit_coords = lat is not None and lon is not None

    # Primary source: coordinate-based (Open-Meteo, or Caiyun if keyed). These
    # track live conditions more reliably than CMA's station feed, which lags
    # during afternoon heat. Resolve coordinates from the city name when needed.
    lat2, lon2, city2, city_ascii = _weather_config(lat, lon, city)
    key = ("coord", round(lat2, 4), round(lon2, 4), city2, city_ascii, bool(CAIYUN_KEY))
    now = time.time()
    cached = _cache.get(key)
    if cached and cached["w"] is not None and now - float(cached["ts"]) < TTL:
        return cached["w"]  # type: ignore

    w = _fetch_caiyun(lat2, lon2, city2, city_ascii) if CAIYUN_KEY else _fetch_openmeteo(lat2, lon2, city2, city_ascii)
    if w is not None:
        _cache[key] = {"w": w, "ts": now}
        return w

    # Fallback: CMA station feed for city-name lookups (no explicit coords).
    if CMA_ENABLED and not explicit_coords and display_city:
        cma_key = ("cma", display_city.casefold())
        cma_cached = _cache.get(cma_key)
        if cma_cached and cma_cached["w"] is not None and now - float(cma_cached["ts"]) < TTL:
            return cma_cached["w"]  # type: ignore
        cma_w = _fetch_cma(display_city)
        if cma_w is not None:
            _cache[cma_key] = {"w": cma_w, "ts": now}
            return cma_w

    return cached["w"] if cached else None  # type: ignore
