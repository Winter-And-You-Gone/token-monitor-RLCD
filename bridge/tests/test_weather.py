from __future__ import annotations

import sys
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

from sources import weather  # noqa: E402


class WeatherConfigTests(unittest.TestCase):
    def test_city_without_coordinates_is_geocoded(self) -> None:
        old_geocode = weather._geocode_city
        weather._geocode_city = lambda city: (35.6895, 139.6917, "Tokyo")
        try:
            lat, lon, city, city_ascii = weather._weather_config(city="Tokyo")

            self.assertEqual(lat, 35.6895)
            self.assertEqual(lon, 139.6917)
            self.assertEqual(city, "Tokyo")
            self.assertEqual(city_ascii, "Tokyo")
        finally:
            weather._geocode_city = old_geocode

    def test_explicit_coordinates_override_city_lookup(self) -> None:
        old_geocode = weather._geocode_city
        weather._geocode_city = lambda city: self.fail("city lookup should not run")
        try:
            lat, lon, city, city_ascii = weather._weather_config(lat=1.25, lon=2.5, city="Tokyo")

            self.assertEqual(lat, 1.25)
            self.assertEqual(lon, 2.5)
            self.assertEqual(city, "Tokyo")
            self.assertEqual(city_ascii, "Tokyo")
        finally:
            weather._geocode_city = old_geocode

    def test_city_ascii_fallback_for_common_chinese_city(self) -> None:
        old_geocode = weather._geocode_city
        weather._geocode_city = lambda city: None
        try:
            _, _, city, city_ascii = weather._weather_config(city="杭州")

            self.assertEqual(city, "杭州")
            self.assertEqual(city_ascii, "Hangzhou")
        finally:
            weather._geocode_city = old_geocode

    def test_cma_station_lookup_uses_autocomplete_result(self) -> None:
        old_request_json = weather._request_json
        old_cache = weather._cma_station_cache
        weather._cma_station_cache = {}
        weather._request_json = lambda url: {
            "data": ["58457|杭州|Hangzhou|中国"]
        }
        try:
            self.assertEqual(weather._cma_station_for_city("杭州"), ("58457", "杭州", "Hangzhou"))
        finally:
            weather._request_json = old_request_json
            weather._cma_station_cache = old_cache

    def test_cma_station_lookup_preserves_ascii_query_label(self) -> None:
        old_request_json = weather._request_json
        old_cache = weather._cma_station_cache
        weather._cma_station_cache = {}
        weather._request_json = lambda url: {
            "data": ["59493|深圳|Shenzuo|中国"]
        }
        try:
            self.assertEqual(weather._cma_station_for_city("SHENZHEN"), ("59493", "深圳", "SHENZHEN"))
        finally:
            weather._request_json = old_request_json
            weather._cma_station_cache = old_cache

    def test_cma_weather_uses_current_temperature(self) -> None:
        old_station = weather._cma_station_for_city
        old_request_json = weather._request_json
        weather._cma_station_for_city = lambda city: ("58457", "杭州", "Hangzhou")
        weather._request_json = lambda url: {
            "data": {
                "location": {"name": "杭州"},
                "now": {"temperature": 23.3},
                "daily": [{"dayText": "小雨", "dayCode": 7, "nightText": "晴", "nightCode": 0}],
                "lastUpdate": "2026/05/29 02:50",
            }
        }
        try:
            result = weather._fetch_cma("杭州")

            self.assertIsNotNone(result)
            self.assertEqual(result.temp_c, 23.3)
            self.assertEqual(result.condition, "Clear")
            self.assertEqual(result.icon, "clear")
            self.assertEqual(result.city, "杭州")
            self.assertEqual(result.city_ascii, "Hangzhou")
        finally:
            weather._cma_station_for_city = old_station
            weather._request_json = old_request_json


if __name__ == "__main__":
    unittest.main()
