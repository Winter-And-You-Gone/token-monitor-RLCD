from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

BRIDGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

import sources.claude_local as claude_local  # noqa: E402
from sources.claude_local import (  # noqa: E402
    _ccusage_args,
    _entries_for_period,
    _model_tokens_today,
    _resolve_ccusage_command,
    _run,
    _subprocess_env_without_proxy,
    _sum_period,
    _utc_period_fallback,
)


class ClaudeLocalTests(unittest.TestCase):
    def tearDown(self) -> None:
        with claude_local._ccusage_lock:
            claude_local._ccusage_cache.clear()
            claude_local._ccusage_inflight.clear()

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
        self.assertIn("--offline", args)

    def test_ccusage_args_adds_offline_to_blocks(self) -> None:
        self.assertEqual(
            _ccusage_args(["blocks", "--active", "--json"]),
            ["blocks", "--active", "--json", "--offline"],
        )

    def test_resolve_ccusage_uses_local_command(self) -> None:
        with mock.patch.dict("os.environ", {"CCUSAGE_CMD": ""}, clear=False):
            os.environ.pop("CCUSAGE_CMD", None)
            with mock.patch.object(claude_local.shutil, "which", return_value="/usr/bin/ccusage"):
                self.assertEqual(_resolve_ccusage_command(), ["/usr/bin/ccusage"])

    def test_resolve_ccusage_rejects_npx_and_latest(self) -> None:
        for command in ("npx -y ccusage@latest", "ccusage@latest"):
            with self.subTest(command=command):
                with mock.patch.dict("os.environ", {"CCUSAGE_CMD": command}, clear=False):
                    with self.assertRaisesRegex(RuntimeError, "npm install -g ccusage"):
                        _resolve_ccusage_command()

    def test_resolve_ccusage_missing_reports_global_install_hint(self) -> None:
        with mock.patch.dict("os.environ", {"CCUSAGE_CMD": ""}, clear=False):
            os.environ.pop("CCUSAGE_CMD", None)
            with mock.patch.object(claude_local.shutil, "which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "npm install -g ccusage"):
                    _resolve_ccusage_command()

    def test_subprocess_env_strips_proxy_vars(self) -> None:
        proxy_env = {
            "HTTP_PROXY": "http://127.0.0.1:7890",
            "HTTPS_PROXY": "http://127.0.0.1:7890",
            "ALL_PROXY": "socks5://127.0.0.1:7890",
            "http_proxy": "http://127.0.0.1:7890",
            "https_proxy": "http://127.0.0.1:7890",
            "all_proxy": "socks5://127.0.0.1:7890",
            "PATH": "keep",
        }
        with mock.patch.dict("os.environ", proxy_env, clear=True):
            env = _subprocess_env_without_proxy()

        for name in claude_local.PROXY_ENV_VARS:
            self.assertNotIn(name, env)
        self.assertEqual(env["PATH"], "keep")

    def test_run_caches_identical_query(self) -> None:
        calls = []

        def fake_execute(args: list[str], timeout: int) -> dict[str, object]:
            calls.append((tuple(args), timeout))
            return {"ok": True}

        with mock.patch.object(claude_local, "_execute_ccusage_uncached", side_effect=fake_execute):
            self.assertEqual(_run(["blocks", "--active", "--json"]), {"ok": True})
            self.assertEqual(_run(["blocks", "--active", "--json"]), {"ok": True})

        self.assertEqual(len(calls), 1)

    def test_run_coalesces_concurrent_identical_query(self) -> None:
        calls = []
        results: list[dict[str, object]] = []

        def fake_execute(args: list[str], timeout: int) -> dict[str, object]:
            calls.append(tuple(args))
            time.sleep(0.05)
            return {"ok": True}

        def worker() -> None:
            results.append(_run(["blocks", "--active", "--json"]))

        with mock.patch.object(claude_local, "_execute_ccusage_uncached", side_effect=fake_execute):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(len(calls), 1)
        self.assertEqual(results, [{"ok": True}, {"ok": True}])

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
