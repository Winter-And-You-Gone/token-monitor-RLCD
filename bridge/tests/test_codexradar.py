from __future__ import annotations

import sys
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

from sources import codexradar  # noqa: E402


def _pt(model: str, effort: str, iq: float) -> dict:
    return {"model": model, "effort": effort, "iq": iq,
            "average_price_usd": 1.0, "average_minutes": 2.0,
            "passed": 100, "total": 336}


class CodexradarExtractTests(unittest.TestCase):
    def test_gpt6_series_and_corner_point(self):
        metrics = {"source_updated_at": "2026-09-28T00:00:00Z", "points": [
            _pt("gpt-6-astra", "ultra", 200),
            _pt("gpt-6-sol", "ultra", 190),
            _pt("gpt-6-sol", "low", 150),
            _pt("gpt-6-luna", "max", 170),
            _pt("gpt-6-luna", "xhigh", 165),
            _pt("gpt-5.6-sol", "max", 180),
            _pt("deepseek-v4-flash", "max", 140),
        ]}
        cr = codexradar._extract(metrics, [])
        got = {(p.model, p.effort): p.iq for p in cr.points}
        self.assertEqual(got[("astra", "ultra")], 200)
        self.assertEqual(got[("sol", "ultra")], 190)
        self.assertEqual(got[("sol", "low")], 150)
        self.assertEqual(got[("luna", "max")], 170)
        self.assertEqual(got[("luna", "xhigh")], 165)
        self.assertEqual(got[("sol5.6", "max")], 180)
        self.assertEqual(got[("deepseek", "max")], 140)
        self.assertNotIn(("gpt-5.5", "xhigh"), got)
        self.assertTrue(cr.available)
        self.assertEqual(cr.updated_at, "2026-09-28T00:00:00Z")

    def test_terra_falls_back_to_gpt56(self):
        metrics = {"points": [
            _pt("gpt-5.6-terra", "ultra", 175),
            _pt("gpt-5.6-terra", "low", 150),
        ]}
        cr = codexradar._extract(metrics, [])
        terra = {(p.effort, p.iq) for p in cr.points if p.model == "terra"}
        self.assertEqual(terra, {("ultra", 175), ("low", 150)})

    def test_terra_prefers_gpt6_once_live(self):
        metrics = {"points": [
            _pt("gpt-6-terra", "ultra", 205),
            _pt("gpt-5.6-terra", "ultra", 175),
            _pt("gpt-5.6-terra", "low", 150),
        ]}
        cr = codexradar._extract(metrics, [])
        terra = {(p.effort, p.iq) for p in cr.points if p.model == "terra"}
        self.assertEqual(terra, {("ultra", 205), ("low", 150)})

    def test_history_trend_uses_terra_fallback(self):
        metrics = {"points": [_pt("gpt-5.6-terra", "max", 175)]}
        history = [
            {"points": [_pt("gpt-5.6-terra", "max", 170)]},
            {"points": [_pt("gpt-5.6-terra", "max", 175)]},
        ]
        cr = codexradar._extract(metrics, history)
        trends = [t for t in cr.trends if t.model == "terra" and t.effort == "max"]
        self.assertEqual(len(trends), 1)
        self.assertEqual(trends[0].iqs, [170.0, 175.0])


if __name__ == "__main__":
    unittest.main()
