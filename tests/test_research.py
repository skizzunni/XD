from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bot.backtest import provenance
from bot.demo import generate
from bot.models import Config, Trade
from bot.report import block_bootstrap, gates, max_drawdown, stats, write_json
from bot.research import pbo, prepare, primary_candidates, run_phase, verify


def trade(day, net=20, gross=25):
    return Trade(
        str(day),
        "P0",
        1,
        "MNQ-TEST",
        str(day) + "T15:30:00-05:00",
        str(day) + "T15:59:00-05:00",
        20000,
        20006.25,
        gross,
        net,
        1,
        3,
        5,
        "TIME_EXIT",
        (),
    )


class ResearchTests(unittest.TestCase):
    def test_cost_stress_and_absolute_hurdles(self):
        cfg = Config(costs_calibrated=True)
        days = [str(date(2025, 1, 1) + timedelta(days=i)) for i in range(200)]
        trades = [trade(d) for d in days]
        metrics = stats(trades)
        checks = gates(metrics, [19, 21], None, 0.2, cfg)
        self.assertTrue(all(checks.values()))
        bad = gates(stats([trade(d, 1, 6) for d in days]), [-1, 3], None, 0.3, cfg)
        self.assertFalse(bad["gross_at_least_twice_base_cost"])
        self.assertFalse(bad["net_at_least_two_ticks"])
        self.assertFalse(bad["bootstrap_lower_bound_positive"])
        self.assertFalse(bad["pbo_at_most_25_percent"])
        self.assertFalse(bad["plus_one_tick_per_side_positive"])

    def test_at_least_150_actual_trades_required(self):
        cfg = Config(costs_calibrated=True)
        checks = gates(stats([trade("2025-01-01")] * 149), [1, 2], None, 0.1, cfg)
        self.assertFalse(checks["minimum_150_trades"])
        with self.assertRaises(ValueError):
            Config(minimum_segment_trades=1)

    def test_drawdown_and_concentration(self):
        self.assertEqual(max_drawdown([10, -5, -8, 4]), 13)
        m = stats([trade("2025-01-01", 100, 105), trade("2025-01-02", 1, 6)])
        self.assertGreater(m["largest_trade_share"], 0.2)
        self.assertFalse(
            gates(m, [1, 2], None, 0.1, Config(costs_calibrated=True))[
                "no_trade_above_20_percent_profit"
            ]
        )

    def test_bootstrap_repeatable_with_no_trade_sessions(self):
        days = [str(date(2025, 1, 1) + timedelta(days=i)) for i in range(30)]
        trades = [trade(d, 20 if i % 4 else -10) for i, d in enumerate(days) if i % 3]
        a = block_bootstrap(trades, days, 500, 5, 9)
        b = block_bootstrap(trades, days, 500, 5, 9)
        self.assertEqual(a, b)
        self.assertLess(a[0], a[1])

    def test_frozen_grid_and_conservative_pbo_ties(self):
        candidates = primary_candidates(Config())
        self.assertEqual(len(candidates), 9)
        self.assertIn(
            (0.1, 0.1), [(c.long_threshold, c.short_threshold) for c in candidates]
        )
        days = [str(date(2025, 1, 1) + timedelta(days=i)) for i in range(16)]
        engines = [SimpleNamespace(trades=[trade(d) for d in days]) for _ in range(9)]
        self.assertEqual(pbo(engines, days), 1)
        self.assertIsNone(pbo(engines, days[:2]))

    def test_synthetic_data_never_qualifies_as_holdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            ticks, calendar = generate(Path(tmp) / "demo", 24)
            with self.assertRaisesRegex(ValueError, "Synthetic"):
                prepare(
                    ticks,
                    calendar,
                    Config(costs_calibrated=True),
                    Path(tmp) / "research",
                )

    def test_changed_data_invalidates_frozen_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ticks, calendar = generate(root / "demo", 24)
            frozen = provenance(ticks, calendar, Config())
            write_json(root / "frozen.json", frozen)
            verify(root)
            with ticks.open("a") as f:
                f.write("\n")
            with self.assertRaisesRegex(ValueError, "tick_checksum"):
                verify(root)

    def test_holdout_consumed_before_any_performance_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ticks, calendar = generate(root / "demo", 24)
            cfg = Config(costs_calibrated=True)
            frozen = provenance(ticks, calendar, cfg)
            frozen["partitions"] = {"holdout": ["2026-02-02", "2026-02-03"]}
            write_json(root / "frozen.json", frozen)
            dev = {
                f: {"config": replace(cfg, strategy=f).to_dict(), "pbo": 0.2}
                for f in ("P0", "C1")
            }
            write_json(root / "development.json", dev)
            write_json(
                root / "validation.json",
                {f: {"gates": {"pass": True}, "metrics": {}} for f in dev},
            )
            with patch(
                "bot.research.replay",
                side_effect=ValueError("simulated interrupted run"),
            ):
                with self.assertRaisesRegex(ValueError, "interrupted"):
                    run_phase(root, "holdout")
            self.assertTrue((root / "HOLDOUT_CONSUMED").exists())
            with self.assertRaises(FileExistsError):
                run_phase(root, "holdout")

    def test_failed_validation_does_not_touch_holdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ticks, calendar = generate(root / "demo", 24)
            cfg = Config(costs_calibrated=True)
            frozen = provenance(ticks, calendar, cfg)
            frozen["partitions"] = {"holdout": ["2026-02-02"]}
            write_json(root / "frozen.json", frozen)
            write_json(root / "development.json", {})
            write_json(root / "validation.json", {"P0": {"gates": {"pass": False}}})
            with self.assertRaisesRegex(ValueError, "No candidate passed"):
                run_phase(root, "holdout")
            self.assertFalse((root / "HOLDOUT_CONSUMED").exists())


if __name__ == "__main__":
    unittest.main()
