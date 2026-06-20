from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fastapi import HTTPException

BRIDGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

import bridge  # noqa: E402


class BridgeConfigTests(unittest.TestCase):
    def test_load_dotenv_keeps_existing_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "RLCD_TEST_VALUE=from-file # comment\n"
                "RLCD_TEST_EXISTING=from-file\n",
                encoding="utf-8",
            )
            os.environ.pop("RLCD_TEST_VALUE", None)
            os.environ["RLCD_TEST_EXISTING"] = "from-env"
            try:
                bridge._load_dotenv(path)

                self.assertEqual(os.environ["RLCD_TEST_VALUE"], "from-file")
                self.assertEqual(os.environ["RLCD_TEST_EXISTING"], "from-env")
            finally:
                os.environ.pop("RLCD_TEST_VALUE", None)
                os.environ.pop("RLCD_TEST_EXISTING", None)

    def test_query_token_disabled_by_default(self) -> None:
        old_auth = bridge.AUTH_TOKEN
        old_allow = bridge.ALLOW_QUERY_TOKEN
        bridge.AUTH_TOKEN = "secret"
        bridge.ALLOW_QUERY_TOKEN = False
        try:
            with self.assertRaises(HTTPException):
                bridge._check_auth(None, "secret")
            bridge._check_auth("secret", None)
        finally:
            bridge.AUTH_TOKEN = old_auth
            bridge.ALLOW_QUERY_TOKEN = old_allow

    def test_query_token_can_be_enabled(self) -> None:
        old_auth = bridge.AUTH_TOKEN
        old_allow = bridge.ALLOW_QUERY_TOKEN
        bridge.AUTH_TOKEN = "secret"
        bridge.ALLOW_QUERY_TOKEN = True
        try:
            bridge._check_auth(None, "secret")
        finally:
            bridge.AUTH_TOKEN = old_auth
            bridge.ALLOW_QUERY_TOKEN = old_allow

    def test_default_bind_host_is_loopback(self) -> None:
        old_auth = bridge.AUTH_TOKEN
        old_host = os.environ.pop("RLCD_HOST", None)
        bridge.AUTH_TOKEN = None
        try:
            self.assertEqual(bridge._validated_bind_host(), "127.0.0.1")
        finally:
            bridge.AUTH_TOKEN = old_auth
            if old_host is not None:
                os.environ["RLCD_HOST"] = old_host

    def test_non_loopback_bind_requires_token(self) -> None:
        old_auth = bridge.AUTH_TOKEN
        old_host = os.environ.get("RLCD_HOST")
        os.environ["RLCD_HOST"] = "0.0.0.0"
        bridge.AUTH_TOKEN = None
        try:
            with self.assertRaises(SystemExit):
                bridge._validated_bind_host()
            bridge.AUTH_TOKEN = "secret"
            self.assertEqual(bridge._validated_bind_host(), "0.0.0.0")
        finally:
            bridge.AUTH_TOKEN = old_auth
            if old_host is None:
                os.environ.pop("RLCD_HOST", None)
            else:
                os.environ["RLCD_HOST"] = old_host

    def test_weather_override_failure_has_short_cooldown(self) -> None:
        old_fetch = bridge.fetch_weather
        old_retry = bridge.WEATHER_OVERRIDE_RETRY_SEC
        old_cache = bridge._weather_override_cache
        old_failures = bridge._weather_override_failures
        old_inflight = bridge._weather_override_inflight
        bridge.fetch_weather = lambda lat=None, lon=None, city=None: None
        bridge.WEATHER_OVERRIDE_RETRY_SEC = 30
        bridge._weather_override_cache = {}
        bridge._weather_override_failures = {}
        bridge._weather_override_inflight = set()
        try:
            key = bridge._weather_override_key(None, None, "Missing City")
            bridge._refresh_weather_override(key, None, None, "Missing City")

            weather, pending = bridge._get_weather_override(None, None, "Missing City")

            self.assertIsNone(weather)
            self.assertFalse(pending)
            self.assertEqual(bridge._weather_override_inflight, set())
        finally:
            bridge.fetch_weather = old_fetch
            bridge.WEATHER_OVERRIDE_RETRY_SEC = old_retry
            bridge._weather_override_cache = old_cache
            bridge._weather_override_failures = old_failures
            bridge._weather_override_inflight = old_inflight

    def test_usage_payload_includes_current_pet_state(self) -> None:
        old_cache = bridge._cache.copy()
        try:
            bridge._reset_pet_state_for_tests()
            bridge._cache["report"] = bridge._mock_report()
            bridge._cache["ts"] = time.time()
            bridge._cache["error"] = None

            bridge._apply_pet_event({
                "event": "PreToolUse",
                "agent": "codex",
                "session_id": "current-pet",
            })

            payload = bridge.get_usage(
                mock=0,
                token=None,
                weather_lat=None,
                weather_lon=None,
                weather_city=None,
                x_rlcd_token=None,
            )

            self.assertEqual(payload["pet"]["state"], "working")
            self.assertEqual(payload["pet"]["agent"], "codex")
        finally:
            bridge._cache.clear()
            bridge._cache.update(old_cache)
            bridge._reset_pet_state_for_tests()

    def test_other_agent_fallback_preserves_codex_usage(self) -> None:
        codex = bridge.OtherAgentUsage(
            agent="codex",
            today=bridge.Bucket(tokens_used=42, cost_usd=0.1),
            month=bridge.Bucket(tokens_used=420, cost_usd=1.0),
            lifetime=bridge.Bucket(tokens_used=4200, cost_usd=10.0),
        )
        old_cache = bridge._cache.copy()
        old_include = bridge.INCLUDE_OTHERS
        old_fetch = bridge.fetch_other_agents
        bridge.INCLUDE_OTHERS = True
        bridge.fetch_other_agents = lambda: []
        try:
            bridge._cache["other"] = [codex]

            rows = bridge._fetch_other_agents_with_fallback()

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].agent, "codex")
            self.assertEqual(rows[0].today.tokens_used, 42)
        finally:
            bridge._cache.clear()
            bridge._cache.update(old_cache)
            bridge.INCLUDE_OTHERS = old_include
            bridge.fetch_other_agents = old_fetch

    def test_refresh_error_backfills_other_agents_into_cached_report(self) -> None:
        codex = bridge.OtherAgentUsage(
            agent="codex",
            today=bridge.Bucket(tokens_used=99, cost_usd=0.2),
            month=bridge.Bucket(tokens_used=990, cost_usd=2.0),
            lifetime=bridge.Bucket(tokens_used=9900, cost_usd=20.0),
        )
        old_cache = bridge._cache.copy()
        old_include = bridge.INCLUDE_OTHERS
        old_fetch_claude = bridge.fetch_claude
        old_fetch_other = bridge.fetch_other_agents
        report = bridge._mock_report().model_copy(update={"other": []})
        bridge.INCLUDE_OTHERS = True
        bridge.fetch_claude = lambda: (_ for _ in ()).throw(TimeoutError("slow ccusage"))
        bridge.fetch_other_agents = lambda: [codex]
        try:
            bridge._cache["report"] = report
            bridge._cache["other"] = []
            bridge._cache["error"] = None

            bridge._refresh_once()

            refreshed = bridge._cache["report"]
            self.assertIsInstance(refreshed, bridge.UsageReport)
            self.assertEqual(refreshed.other[0].agent, "codex")
            self.assertEqual(refreshed.other[0].today.tokens_used, 99)
            self.assertIn("TimeoutError", bridge._cache["error"])
        finally:
            bridge._cache.clear()
            bridge._cache.update(old_cache)
            bridge.INCLUDE_OTHERS = old_include
            bridge.fetch_claude = old_fetch_claude
            bridge.fetch_other_agents = old_fetch_other

    def test_mock_usage_payload_includes_current_pet_state(self) -> None:
        try:
            bridge._reset_pet_state_for_tests()
            bridge._apply_pet_event({
                "event": "PreToolUse",
                "agent": "claude",
                "session_id": "mock-current-pet",
            })

            payload = bridge.get_usage(
                mock=1,
                token=None,
                weather_lat=None,
                weather_lon=None,
                weather_city=None,
                x_rlcd_token=None,
            )

            self.assertEqual(payload["pet"]["state"], "working")
            self.assertEqual(payload["pet"]["agent"], "claude-code")
        finally:
            bridge._reset_pet_state_for_tests()


if __name__ == "__main__":
    unittest.main()
