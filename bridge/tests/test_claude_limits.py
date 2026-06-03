from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

from sources import claude_limits  # noqa: E402


class ClaudeLimitsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "claude-limits.json"
        self.old_file = claude_limits.LIMITS_FILE
        self.old_stale = claude_limits.STALE_AFTER
        claude_limits.LIMITS_FILE = str(self.path)
        claude_limits.STALE_AFTER = 600

    def tearDown(self) -> None:
        claude_limits.LIMITS_FILE = self.old_file
        claude_limits.STALE_AFTER = self.old_stale
        self.tmpdir.cleanup()

    def write_limits(self, **overrides: object) -> None:
        payload = {
            "util_5h": 0.25,
            "util_7d": 0.5,
            "reset_5h": int(time.time()) + 3600,
            "reset_7d": int(time.time()) + 86400,
            "status": "ok",
            "ts": int(time.time()),
        }
        payload.update(overrides)
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def test_ok_status_when_fresh(self) -> None:
        self.write_limits()

        limits = claude_limits.fetch_limits()

        self.assertIsNotNone(limits)
        self.assertEqual(limits.status, "ok")
        self.assertEqual(limits.util_5h, 0.25)

    def test_preserves_fresh_error_status(self) -> None:
        self.write_limits(status="err:HTTPError")

        limits = claude_limits.fetch_limits()

        self.assertIsNotNone(limits)
        self.assertEqual(limits.status, "err:HTTPError")
        self.assertEqual(limits.util_5h, 0.25)

    def test_stale_status_overrides_old_ok(self) -> None:
        self.write_limits(ts=int(time.time()) - 1000)

        limits = claude_limits.fetch_limits()

        self.assertIsNotNone(limits)
        self.assertEqual(limits.status, "stale")


if __name__ == "__main__":
    unittest.main()
