from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

from sources import zcode_usage  # noqa: E402
from sources.claude_local import LOCAL_TZ  # noqa: E402


def _insert_usage(con: sqlite3.Connection, rows: list[tuple]) -> None:
    con.execute(
        "CREATE TABLE model_usage ("
        "id INTEGER PRIMARY KEY, started_at INTEGER, model_id TEXT, "
        "computed_total_tokens INTEGER, input_tokens INTEGER, output_tokens INTEGER, "
        "cache_read_input_tokens INTEGER)"
    )
    con.executemany("INSERT INTO model_usage VALUES (NULL, ?, ?, ?, ?, ?, ?)", rows)
    con.commit()


class ZcodeUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "db.sqlite"
        self._orig = zcode_usage.ZCODE_DB_PATH
        zcode_usage.ZCODE_DB_PATH = self.db_path

    def tearDown(self) -> None:
        zcode_usage.ZCODE_DB_PATH = self._orig
        self._tmp.cleanup()

    def _usage(self):
        return zcode_usage.fetch_zcode_usage()

    def test_aggregates_buckets_and_models(self) -> None:
        today_ms = int(datetime.now(LOCAL_TZ).timestamp() * 1000)
        last_month_ms = int((datetime.now(LOCAL_TZ) - timedelta(days=45)).timestamp() * 1000)
        con = sqlite3.connect(self.db_path)
        _insert_usage(con, [
            # computed_total present: input+output.
            (today_ms, "GLM-5.3-Flash", 700, 500, 200, 0),
            # computed_total null: falls back to input+output.
            (today_ms, "deepseek-v4-flash", None, 1000, 0, 0),
            (last_month_ms, "GLM-5.3", 3100, 0, 0, 0),
            # Zero-token rows are skipped.
            (today_ms, "gpt-5.6-luna", 0, 0, 0, 0),
        ])
        con.close()

        usage = self._usage()
        self.assertIsNotNone(usage)
        self.assertEqual(usage.agent, "zcode")
        self.assertEqual(usage.today.tokens_used, 1700)
        # 45 days back is always in a different month, so month == today.
        self.assertEqual(usage.month.tokens_used, 1700)
        self.assertEqual(usage.lifetime.tokens_used, 4800)
        self.assertEqual([m.model for m in usage.by_model], ["GLM-5.3", "deepseek-v4-flash", "GLM-5.3-Flash"])

    def test_unpriced_models_cost_zero(self) -> None:
        today_ms = int(datetime.now(LOCAL_TZ).timestamp() * 1000)
        con = sqlite3.connect(self.db_path)
        _insert_usage(con, [(today_ms, "muse-spark-1.2-contributor", 5000, 0, 0, 0)])
        con.close()
        usage = self._usage()
        # No official price exists for this model.
        self.assertEqual(usage.lifetime.cost, 0.0)

    def test_cost_official_prices(self) -> None:
        # GLM-5.3-Flash at the 50% promo CNY rate (valid before 2026-09-09):
        # 1M miss input * ¥0.4 + 1M output * ¥1.4.
        promo_ms = int(datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
        # deepseek-v4-flash at peak with zcode's input-includes-cache
        # semantics: 1M input of which 800k are cache reads -> 200k miss.
        # 0.2M*¥3 + 0.8M*¥0.1 + 0.5M*¥9 = ¥0.6 + ¥0.08 + ¥4.5.
        peak_ms = int(datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc).timestamp() * 1000)
        con = sqlite3.connect(self.db_path)
        _insert_usage(con, [
            (promo_ms, "GLM-5.3-Flash", 2_000_000, 1_000_000, 1_000_000, 0),
            (peak_ms, "deepseek-v4-flash", 1_500_000, 1_000_000, 500_000, 800_000),
        ])
        con.close()
        usage = self._usage()
        self.assertEqual(usage.currency, "CNY")
        self.assertAlmostEqual(usage.by_model[0].cost, 0.4 + 1.4, places=6)
        self.assertAlmostEqual(usage.by_model[1].cost, 0.6 + 0.08 + 4.5, places=6)
        self.assertAlmostEqual(usage.lifetime.cost, 0.4 + 1.4 + 0.6 + 0.08 + 4.5, places=6)

    def test_missing_db_returns_none(self) -> None:
        zcode_usage.ZCODE_DB_PATH = self.db_path.parent / "missing.sqlite"
        self.assertIsNone(self._usage())

    def test_broken_db_returns_none(self) -> None:
        self.db_path.write_bytes(b"this is not a sqlite database" * 10)
        self.assertIsNone(self._usage())


if __name__ == "__main__":
    unittest.main()
