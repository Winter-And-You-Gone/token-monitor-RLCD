from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BRIDGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

from sources.claude_local import (  # noqa: E402
    _ccusage_args,
    _entries_for_period,
    _model_tokens_today,
    _parse_token_limit,
    _sum_period,
    _utc_period_fallback,
)


class ClaudeLocalTests(unittest.TestCase):
    def test_parse_token_limit_suffixes(self) -> None:
        self.assertEqual(_parse_token_limit("100M"), 100_000_000)
        self.assertEqual(_parse_token_limit("1.5B"), 1_500_000_000)
        self.assertEqual(_parse_token_limit("250k"), 250_000)
        self.assertEqual(_parse_token_limit("500000"), 500_000)

    def test_parse_token_limit_disabled_or_invalid(self) -> None:
        self.assertIsNone(_parse_token_limit("0"))
        self.assertIsNone(_parse_token_limit("off"))
        self.assertIsNone(_parse_token_limit("not-a-limit"))

    def test_sum_period_accepts_agent_cost_usd(self) -> None:
        tokens, cost = _sum_period([
            {"totalTokens": 100, "costUSD": 1.25},
            {"totalTokens": 50, "costUSD": 0.75},
        ])

        self.assertEqual(tokens, 150)
        self.assertEqual(cost, 2.0)

    def test_sum_period_accepts_unified_total_cost(self) -> None:
        tokens, cost = _sum_period([
            {"totalTokens": 100, "totalCost": 1.25},
            {"totalTokens": 50, "totalCost": 0.75},
        ])

        self.assertEqual(tokens, 150)
        self.assertEqual(cost, 2.0)

    def test_ccusage_args_adds_timezone_to_period_commands(self) -> None:
        args = _ccusage_args(["claude", "daily", "--json"])

        self.assertIn("--timezone", args)

    def test_ccusage_args_leaves_blocks_untouched(self) -> None:
        self.assertEqual(
            _ccusage_args(["blocks", "--active", "--json"]),
            ["blocks", "--active", "--json"],
        )

    def test_utc_period_fallback_when_local_day_is_ahead(self) -> None:
        now = datetime(2026, 6, 1, 3, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))

        self.assertEqual(_utc_period_fallback(now, "%Y-%m-%d"), "2026-05-31")
        self.assertEqual(_utc_period_fallback(now, "%Y-%m"), "2026-05")

    def test_entries_for_period_uses_fallback_only_when_exact_missing(self) -> None:
        entries = [
            {"date": "2026-05-31", "totalTokens": 100},
            {"date": "2026-06-01", "totalTokens": 200},
        ]

        self.assertEqual(
            _entries_for_period(entries, "2026-06-01", "2026-05-31"),
            [{"date": "2026-06-01", "totalTokens": 200}],
        )
        self.assertEqual(
            _entries_for_period(entries[:1], "2026-06-01", "2026-05-31"),
            [{"date": "2026-05-31", "totalTokens": 100}],
        )

    def test_model_tokens_today_accepts_model_breakdowns(self) -> None:
        tokens = _model_tokens_today([
            {
                "period": "2026-06-01",
                "modelBreakdowns": [
                    {
                        "modelName": "deepseek-chat",
                        "inputTokens": 100,
                        "outputTokens": 50,
                        "cacheCreationTokens": 25,
                        "cacheReadTokens": 10,
                    },
                ],
            },
        ], "deepseek", "2026-06-01")

        self.assertEqual(tokens, 185)

    def test_model_tokens_today_accepts_models_map(self) -> None:
        tokens = _model_tokens_today([
            {
                "period": "2026-06-01",
                "models": {
                    "deepseek-chat": {
                        "inputTokens": 100,
                        "outputTokens": 50,
                        "cachedInputTokens": 25,
                        "reasoningOutputTokens": 10,
                    },
                    "claude-sonnet-4-6": {
                        "inputTokens": 999,
                    },
                },
            },
        ], "deepseek", "2026-06-01")

        self.assertEqual(tokens, 185)

    def test_model_tokens_today_uses_fallback_when_exact_missing(self) -> None:
        tokens = _model_tokens_today([
            {
                "date": "2026-05-31",
                "models": {
                    "deepseek-chat": {
                        "totalTokens": 321,
                    },
                },
            },
        ], "deepseek", "2026-06-01", "2026-05-31")

        self.assertEqual(tokens, 321)


if __name__ == "__main__":
    unittest.main()
