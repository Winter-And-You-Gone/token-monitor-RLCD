from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

from sources import dsh_usage  # noqa: E402
from sources import model_prices  # noqa: E402
from sources.claude_local import LOCAL_TZ  # noqa: E402

try:
    import zstandard
    HAVE_ZSTD = True
except Exception:
    HAVE_ZSTD = False


def _msg_event(ts_ms: int, model: str, usage: dict) -> dict:
    return {
        "type": "assistant/message",
        "seq": 1,
        "time": ts_ms,
        "data": {
            "turn": 1,
            "step": 1,
            "message": {"role": "assistant", "source": {"kind": "model", "model": model}},
            "usage": usage,
        },
    }


def _noise_events(ts_ms: int) -> list[dict]:
    return [
        {"type": "reasoning-chunks", "time": ts_ms, "data": {"chunks": [{"text": '"assistant/message" inside content'}]}},
        {"type": "step/end", "time": ts_ms, "data": {"turn": 1, "step": 1}},
        {"type": "turn/end", "time": ts_ms, "data": {"turn": 1, "reason": {"kind": "completed"}}},
    ]


class DshUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.session_dir = Path(self._tmp.name)
        self._orig_dir = dsh_usage.DSH_SESSION_DIR
        dsh_usage.DSH_SESSION_DIR = self.session_dir
        dsh_usage._file_cache.clear()
        dsh_usage._usage_cache.update(d=None, ts=0.0, busy=False)

    def tearDown(self) -> None:
        dsh_usage.DSH_SESSION_DIR = self._orig_dir
        dsh_usage._file_cache.clear()
        dsh_usage._usage_cache.update(d=None, ts=0.0, busy=False)
        self._tmp.cleanup()

    def _write_session(self, uuid: str, events: list[dict], *, zstd: bool = False) -> Path:
        session_dir = self.session_dir / "ws" / f"session-{uuid}"
        session_dir.mkdir(parents=True, exist_ok=True)
        payload = "".join(json.dumps(e) + "\n" for e in events).encode("utf-8")
        if zstd:
            assert HAVE_ZSTD
            # Mimic dsh durable publishes: several concatenated zstd frames,
            # each with the content size omitted from the frame header.
            cctx = zstandard.ZstdCompressor(write_content_size=False)
            payload = cctx.compress(payload[: len(payload) // 2]) + cctx.compress(payload[len(payload) // 2:])
            target = session_dir / "session.jsonl.zstd"
        else:
            target = session_dir / "session.jsonl"
        target.write_bytes(payload)
        return target

    def _fresh_usage(self):
        dsh_usage._usage_cache.update(d=None, ts=0.0, busy=False)
        dsh_usage._refresh_worker()
        return dsh_usage._usage_cache["d"]

    def test_aggregates_buckets_and_models(self) -> None:
        now_ms = int(time.time() * 1000)
        last_month = datetime.now(LOCAL_TZ) - timedelta(days=45)
        events = [
            *_noise_events(now_ms),
            _msg_event(now_ms, "deepseek-v4-flash", {"inputTokens": 1000, "outputTokens": 500, "cacheReadTokens": 200}),
            _msg_event(int(last_month.timestamp() * 1000), "deepseek-v4-flash", {"inputTokens": 3000, "outputTokens": 100}),
            _msg_event(int(last_month.timestamp() * 1000), "qwen3.8-flash", {"inputTokens": 10, "outputTokens": 20}),
        ]
        self._write_session("0" * 8 + "-aaaa", events)

        usage = self._fresh_usage()

        self.assertIsNotNone(usage)
        self.assertEqual(usage.agent, "dsh")
        self.assertEqual(usage.today.tokens_used, 1700)
        # 45 days back is always in a different month, so month == today.
        self.assertEqual(usage.month.tokens_used, 1700)
        self.assertEqual(usage.lifetime.tokens_used, 4830)
        self.assertEqual([m.model for m in usage.by_model], ["deepseek-v4-flash", "qwen3.8-flash"])
        self.assertEqual(usage.by_model[0].tokens, 4800)

    def test_skips_events_without_usage(self) -> None:
        now_ms = int(time.time() * 1000)
        self._write_session(
            "bbbb",
            [
                *_noise_events(now_ms),
                {"type": "assistant/message", "time": now_ms, "data": {"message": {}}},
                {"type": "assistant/message", "time": now_ms, "data": {"usage": {"inputTokens": 0, "outputTokens": 0}}},
            ],
        )
        usage = self._fresh_usage()
        self.assertIsNotNone(usage)
        self.assertEqual(usage.lifetime.tokens_used, 0)
        self.assertEqual(usage.by_model, [])

    def test_model_cost_peak_offpeak(self) -> None:
        prices = model_prices.load_prices("RLCD_DSH_PRICES")
        # Wed 2026-09-02 02:00 UTC is inside the 01:00-04:00 UTC peak window;
        # Sat 2026-09-05 02:00 UTC is a weekend, so off-peak applies.
        # DeepSeek CNY list: peak ¥3.0 miss input / ¥9.0 output, off-peak half.
        peak = model_prices.model_cost("deepseek-v4-flash", 1_000_000, 0, 1_000_000,
                                       datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc), prices)
        off = model_prices.model_cost("deepseek-v4-flash", 1_000_000, 0, 1_000_000,
                                      datetime(2026, 9, 5, 2, 0, tzinfo=timezone.utc), prices)
        self.assertAlmostEqual(peak[0], 3.0 + 9.0, places=6)
        self.assertEqual(peak[1], "CNY")
        self.assertAlmostEqual(off[0], 1.5 + 4.5, places=6)
        self.assertAlmostEqual(peak[0], off[0] * 2, places=6)
        # Unpriced models stay free (CNY default currency).
        self.assertEqual(
            model_prices.model_cost("some-relay-only-model", 1_000_000, 0, 1_000_000,
                                    datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc), prices),
            (0.0, "CNY"),
        )

    def test_glm_promo_pricing(self) -> None:
        prices = model_prices.load_prices("RLCD_DSH_PRICES")
        # GLM-5.3-Flash bigmodel CNY list ¥0.8 / ¥2.8; 50% promo until
        # 2026-09-09 16:00 UTC.
        during = model_prices.model_cost("glm-5.3-flash", 1_000_000, 0, 1_000_000,
                                         datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc), prices)
        after = model_prices.model_cost("glm-5.3-flash", 1_000_000, 0, 1_000_000,
                                        datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc), prices)
        self.assertAlmostEqual(during[0], 0.4 + 1.4, places=6)
        self.assertEqual(during[1], "CNY")
        self.assertAlmostEqual(after[0], 0.8 + 2.8, places=6)
        # Longest substring match: "glm-5.3-flash" must not fall back to the
        # more expensive glm-5.3 list price.
        self.assertEqual(during, model_prices.model_cost("GLM-5.3-Flash", 1_000_000, 0, 1_000_000,
                                                         datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc), prices))

    def test_alias_and_native_currencies(self) -> None:
        prices = model_prices.load_prices("RLCD_DSH_PRICES")
        when = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        # ox-alpha is an internal id for GLM-5.3-Flash: same price, including
        # the active promotion.
        self.assertEqual(
            model_prices.model_cost("ox-alpha", 1_000_000, 0, 1_000_000, when, prices),
            model_prices.model_cost("glm-5.3-flash", 1_000_000, 0, 1_000_000, when, prices),
        )
        # Hy3 CNY list (¥1 / ¥0.25 cache-hit / ¥4 per 1M), native CNY.
        self.assertEqual(
            model_prices.model_cost("hy3", 1_000_000, 500_000, 1_000_000, when, prices),
            (1.0 + 0.125 + 4.0, "CNY"),
        )
        # Meta Muse Spark 1.2 contributor tier stays in its native USD.
        self.assertEqual(
            model_prices.model_cost("muse-spark-1.2-contributor", 1_000_000, 0, 1_000_000, when, prices),
            (0.30, "USD"),
        )
        # GLM-5.3 flagship has only a USD (Z.ai) official price.
        self.assertEqual(
            model_prices.model_cost("glm-5.3", 1_000_000, 0, 1_000_000, when, prices)[1],
            "USD",
        )
        # Alibaba Qwen3.8-Flash: CNY list ¥0.8 / ¥2.7 per 1M, native CNY.
        self.assertEqual(
            model_prices.model_cost("qwen3.8-flash", 1_000_000, 0, 1_000_000, when, prices),
            (0.8 + 2.7, "CNY"),
        )
        # Aggregation converts foreign entries into the panels' CNY display.
        self.assertAlmostEqual(model_prices.to_cny(0.30, "USD"), 0.30 * model_prices.CNY_PER_USD, places=9)
        self.assertAlmostEqual(model_prices.to_cny(5.0, "CNY"), 5.0, places=9)

    def test_peak_window_boundaries(self) -> None:
        utc = timezone.utc
        is_peak = model_prices.is_peak_time
        self.assertTrue(is_peak(datetime(2026, 8, 31, 1, 0, tzinfo=utc)))    # Mon 01:00
        self.assertFalse(is_peak(datetime(2026, 8, 31, 0, 59, tzinfo=utc)))  # Mon 00:59
        self.assertFalse(is_peak(datetime(2026, 8, 31, 4, 0, tzinfo=utc)))   # Mon 04:00
        self.assertTrue(is_peak(datetime(2026, 9, 1, 6, 0, tzinfo=utc)))     # Tue 06:00
        self.assertTrue(is_peak(datetime(2026, 9, 4, 9, 59, tzinfo=utc)))    # Fri 09:59
        self.assertFalse(is_peak(datetime(2026, 9, 4, 10, 0, tzinfo=utc)))   # Fri 10:00
        self.assertFalse(is_peak(datetime(2026, 9, 5, 2, 0, tzinfo=utc)))    # Sat
        self.assertFalse(is_peak(datetime(2026, 9, 6, 7, 0, tzinfo=utc)))    # Sun

    def test_cost_from_price_table_end_to_end(self) -> None:
        # 1M cache-miss input + 1M output at official v4-flash CNY rates:
        # peak event ¥12, off-peak event ¥6.
        peak_ms = int(datetime(2026, 9, 2, 2, 30, tzinfo=timezone.utc).timestamp() * 1000)
        off_ms = int(datetime(2026, 9, 5, 12, 30, tzinfo=timezone.utc).timestamp() * 1000)
        self._write_session(
            "cccc",
            [
                _msg_event(peak_ms, "deepseek-v4-flash", {"inputTokens": 1_000_000, "outputTokens": 1_000_000}),
                _msg_event(off_ms, "deepseek-v4-flash", {"inputTokens": 1_000_000, "outputTokens": 1_000_000}),
                _msg_event(peak_ms, "totally-unknown-model", {"inputTokens": 5_000_000, "outputTokens": 1_000_000}),
            ],
        )
        usage = self._fresh_usage()
        self.assertEqual(usage.currency, "CNY")
        self.assertAlmostEqual(usage.lifetime.cost, 12.0 + 6.0, places=3)
        # by_model is token-sorted; the unknown model (6M) outranks v4-flash.
        costs = {m.model: m.cost for m in usage.by_model}
        self.assertAlmostEqual(costs["deepseek-v4-flash"], 12.0 + 6.0, places=3)
        self.assertAlmostEqual(costs["totally-unknown-model"], 0.0, places=6)

    def test_env_price_override(self) -> None:
        peak_ms = int(datetime(2026, 9, 2, 2, 30, tzinfo=timezone.utc).timestamp() * 1000)
        off_ms = int(datetime(2026, 9, 5, 12, 30, tzinfo=timezone.utc).timestamp() * 1000)
        events = [
            _msg_event(peak_ms, "my-relay-model", {"inputTokens": 1_000_000}),
            _msg_event(off_ms, "my-relay-model", {"inputTokens": 1_000_000}),
        ]
        self._write_session("dddd", events)
        env_key = "RLCD_DSH_PRICES"
        old = os.environ.get(env_key)
        try:
            # Flat shape applies to both periods.
            os.environ[env_key] = json.dumps({"my-relay": {"input": 0.5}})
            usage = self._fresh_usage()
            self.assertAlmostEqual(usage.lifetime.cost, 1.0, places=6)
            # Split shape distinguishes peak/off-peak.
            os.environ[env_key] = json.dumps({
                "my-relay": {"peak": {"input": 1.0}, "off_peak": {"input": 0.1}},
            })
            usage = self._fresh_usage()
            self.assertAlmostEqual(usage.lifetime.cost, 1.1, places=6)
        finally:
            if old is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = old

    def test_incremental_cache_reuses_unchanged_files(self) -> None:
        now_ms = int(time.time() * 1000)
        target = self._write_session("dddd", [_msg_event(now_ms, "deepseek-chat", {"inputTokens": 10})])
        self._fresh_usage()
        self.assertIn(target, dsh_usage._file_cache)
        cached_rows = dsh_usage._file_cache[target].rows

        # Second fetch without touching the file must serve the same rows.
        usage = dsh_usage.fetch_dsh_usage()
        self.assertEqual(usage.lifetime.tokens_used, 10)
        self.assertIs(dsh_usage._file_cache[target].rows, cached_rows)

        # Appending a new frame (new size/mtime) must re-decode the file.
        time.sleep(0.01)
        with target.open("ab") as fh:
            fh.write(json.dumps(_msg_event(now_ms + 1, "deepseek-chat", {"inputTokens": 15})).encode("utf-8") + b"\n")
        usage = self._fresh_usage()
        self.assertEqual(usage.lifetime.tokens_used, 25)

    def test_no_session_dir_returns_zero_usage(self) -> None:
        dsh_usage.DSH_SESSION_DIR = self.session_dir / "missing"
        dsh_usage._usage_cache.update(d=None, ts=0.0, busy=False)
        dsh_usage._refresh_worker()
        usage = dsh_usage._usage_cache["d"]
        self.assertIsNotNone(usage)
        self.assertEqual(usage.lifetime.tokens_used, 0)
        self.assertEqual(usage.by_model, [])

    def test_fetch_serves_stale_until_background_scan_lands(self) -> None:
        now_ms = int(time.time() * 1000)
        self._write_session("ffff", [_msg_event(now_ms, "deepseek-chat", {"inputTokens": 42})])
        # Cold cache: fetch must not block and returns None while the scan
        # runs on its background thread.
        first = dsh_usage.fetch_dsh_usage()
        self.assertIsNone(first)
        deadline = time.time() + 5
        while time.time() < deadline and dsh_usage._usage_cache["busy"]:
            time.sleep(0.01)
        self.assertFalse(dsh_usage._usage_cache["busy"])
        second = dsh_usage.fetch_dsh_usage()
        self.assertIsNotNone(second)
        self.assertEqual(second.lifetime.tokens_used, 42)

    @unittest.skipUnless(HAVE_ZSTD, "zstandard not installed")
    def test_concatenated_zstd_frames(self) -> None:
        now_ms = int(time.time() * 1000)
        self._write_session(
            "eeee",
            [_msg_event(now_ms, "deepseek-chat", {"inputTokens": 700, "outputTokens": 70})],
            zstd=True,
        )
        usage = self._fresh_usage()
        self.assertEqual(usage.lifetime.tokens_used, 770)


if __name__ == "__main__":
    unittest.main()
