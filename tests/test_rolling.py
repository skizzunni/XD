import csv
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

from bot.backtest import replay
from bot.dashboard import native_payload, render
from bot.data import Calendar, parse_time, read_ticks
from bot.demo import generate_rolling
from bot.engine import Engine
from bot.learning import QualityLearner
from bot.models import Config, TICK_VALUE
from bot.report import write_run
from bot.research import prepare
from bot.strategy import rolling_setup


class RollingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ticks, self.path = generate_rolling(self.root / "fixture", 1)
        self.calendar = Calendar(self.path)
        self.session = next(iter(self.calendar.sessions.values()))

    def tearDown(self):
        self.tmp.cleanup()

    def engine(self, config=None, ticks=None, calendar=None):
        engine = Engine(config or Config(strategy="R1"), calendar or self.calendar)
        engine.daily_ranges = [100.0] * 20
        for tick in ticks or read_ticks(self.ticks):
            engine.process(tick)
        engine.finish()
        return engine

    def test_more_than_five_fresh_trades_one_position_and_unique_order_ids(self):
        engine = self.engine()
        self.assertEqual(len(engine.trades), 12)
        ids = [e["order_id"] for e in engine.events if e["reason"] == "FILLED"]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(engine.learner.samples), 12)
        self.assertTrue(
            all(
                a["rolling_efficiency"] >= a["learning_at_entry"]["min_efficiency"]
                for a in engine.trade_audits
            )
        )
        for a, b in zip(engine.trades, engine.trades[1:]):
            self.assertLess(parse_time(a.exit_time), parse_time(b.entry_time))
        self.assertTrue(all(t.exit_reason == "TARGET_EXIT" for t in engine.trades))
        self.assertEqual(
            engine.day_realized, sum(t.net_ticks * TICK_VALUE for t in engine.trades)
        )

    def test_repeat_short_entries_are_symmetric_and_train_the_short_side(self):
        rows = [replace(t, price=40000 - t.price) for t in read_ticks(self.ticks)]
        engine = self.engine(ticks=rows)
        self.assertEqual(len(engine.trades), 12)
        self.assertTrue(all(t.direction == -1 for t in engine.trades))
        self.assertTrue(all(t.net_ticks > 0 for t in engine.trades))
        self.assertEqual(
            engine.learner.state(-1, self.session.close)["observations"], 8
        )
        self.assertEqual(engine.learner.state(1, self.session.close)["observations"], 0)

    def test_reentry_after_stop_and_every_loss_has_independent_review(self):
        ticks, _ = generate_rolling(self.root / "losses", 1, losses=True)
        engine = self.engine(
            Config(strategy="R1", session_loss_budget_usd=1000), read_ticks(ticks)
        )
        self.assertEqual(len(engine.trades), 12)
        self.assertTrue(all(t.net_ticks < 0 for t in engine.trades))
        write_run(engine, self.root / "run", {})
        reviews = json.loads((self.root / "run/loss_reviews.json").read_text())
        self.assertEqual(len(reviews), 12)
        for review in reviews:
            self.assertEqual(len(review["diagnostics"]), 30)
            frequency = next(
                c for c in review["diagnostics"] if c["category"] == "Frequency"
            )
            self.assertEqual(frequency["status"], "pass")
            protection = next(
                c
                for c in review["diagnostics"]
                if c["question"] == "Was protection activated immediately after entry?"
            )
            self.assertEqual(protection["status"], "pass")
        self.assertTrue(engine.learner.state(1, self.session.close)["tightened"])

    def test_remaining_daily_budget_stops_further_risk_after_multiple_losses(self):
        ticks, _ = generate_rolling(self.root / "losses", 1, losses=True)
        engine = self.engine(
            Config(strategy="R1", session_loss_budget_usd=50), read_ticks(ticks)
        )
        self.assertEqual(len(engine.trades), 2)
        self.assertGreaterEqual(engine.day_realized, -50)
        self.assertTrue(
            any(e.get("detail") == "STOP_EXCEEDS_LOSS_BUDGET" for e in engine.events)
        )

    def test_evaluation_target_is_cumulative_and_funded_has_no_profit_target(self):
        limited = self.engine(Config(strategy="R1", daily_profit_target_usd=40))
        funded = self.engine()
        self.assertEqual(len(limited.trades), 2)
        self.assertGreaterEqual(limited.day_realized, 40)
        self.assertEqual(len(funded.trades), 12)

    def test_future_outcomes_do_not_affect_current_learning(self):
        learner = QualityLearner()
        for i in range(8):
            learner.record(str(i), self.session.at(14, i), 1, -10)
        self.assertEqual(
            learner.state(1, self.session.at(13, 0))["min_efficiency"], 0.4
        )
        self.assertEqual(learner.state(1, self.session.at(14, 7))["observations"], 7)
        self.assertAlmostEqual(
            learner.state(1, self.session.at(14, 8))["min_efficiency"], 0.55
        )
        self.assertEqual(
            learner.state(-1, self.session.at(15, 0))["min_efficiency"], 0.4
        )
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            learner.record("0", self.session.at(15, 0), 1, 100)

    def test_tightening_is_bounded_and_recovers_only_after_completed_results(self):
        learner = QualityLearner(0.85)
        for i in range(8):
            learner.record(str(i), self.session.at(10, i), 1, -1)
        self.assertEqual(
            learner.state(1, self.session.at(11, 0))["min_efficiency"], 0.9
        )
        for i in range(8):
            learner.record("win" + str(i), self.session.at(12, i), 1, 10)
        self.assertEqual(
            learner.state(1, self.session.at(13, 0))["min_efficiency"], 0.85
        )
        fixed = QualityLearner(enabled=False)
        for i in range(8):
            fixed.record(str(i), self.session.at(10, i), 1, -10)
        self.assertEqual(fixed.state(1, self.session.at(11, 0))["min_efficiency"], 0.4)

    def test_no_entry_inside_scheduled_news_windows_and_scan_resumes(self):
        raw = json.loads(self.path.read_text())
        raw["sessions"][0]["news"] = {
            "flags": ["FOMC"],
            "releases": [
                {
                    "name": "Test release",
                    "timestamp": self.session.at(10, 0).isoformat(),
                    "revision": None,
                }
            ],
        }
        self.path.write_text(json.dumps(raw))
        calendar = Calendar(self.path)
        engine = self.engine(calendar=calendar)
        self.assertEqual(len(engine.trades), 11)
        self.assertEqual(
            parse_time(engine.trades[0].entry_time), self.session.at(10, 30)
        )
        self.assertTrue(
            all(
                not calendar.sessions[self.session.day].news_paused(
                    parse_time(t.entry_time)
                )
                for t in engine.trades
            )
        )

    def test_open_position_flattens_before_scheduled_release_then_resumes(self):
        raw = json.loads(self.path.read_text())
        raw["sessions"][0]["news"] = {
            "flags": ["FOMC"],
            "releases": [
                {
                    "name": "Test release",
                    "timestamp": self.session.at(10, 10).isoformat(),
                    "revision": None,
                }
            ],
        }
        self.path.write_text(json.dumps(raw))
        rows = [
            replace(t, price=20013) if t.timestamp == self.session.at(10, 1) else t
            for t in read_ticks(self.ticks)
        ]
        engine = self.engine(ticks=rows, calendar=Calendar(self.path))
        self.assertEqual(engine.trades[0].exit_reason, "NEWS_EXIT")
        self.assertEqual(parse_time(engine.trades[0].exit_time), self.session.at(10, 5))
        self.assertGreater(len(engine.trades), 1)

    def test_late_news_does_not_enable_warmup_dates(self):
        raw = json.loads(self.path.read_text())
        raw["sessions"][0]["trade_enabled"] = False
        raw["sessions"][0]["news"] = {
            "flags": ["FOMC"],
            "releases": [
                {
                    "name": "Test release",
                    "timestamp": self.session.at(15, 30).isoformat(),
                    "revision": None,
                }
            ],
        }
        self.path.write_text(json.dumps(raw))
        engine = self.engine(calendar=Calendar(self.path))
        self.assertFalse(engine.trades)
        self.assertEqual(len(engine.daily_ranges), 21)

    def test_closed_bar_rules_reject_chop_and_missing_bars(self):
        engine = self.engine()
        bars = engine.bars[:6]
        self.assertIsNotNone(rolling_setup(bars, self.session, 100))
        self.assertIsNone(rolling_setup(bars, self.session, 100, min_efficiency=0.90))
        bad = [replace(b) for b in bars]
        bad[2].end += timedelta(minutes=5)
        self.assertIsNone(rolling_setup(bad, self.session, 100))

    def test_prior_losses_change_actual_entry_filter_without_using_future_data(self):
        ticks, _ = generate_rolling(self.root / "choppy", 1, choppy=True)
        base = self.engine(ticks=read_ticks(ticks))
        trained = Engine(Config(strategy="R1"), self.calendar)
        trained.daily_ranges = [100] * 20
        for i in range(8):
            trained.learner.record(
                "past" + str(i), self.session.open - timedelta(minutes=20 - i), 1, -10
            )
        for tick in read_ticks(ticks):
            trained.process(tick)
        trained.finish()
        self.assertGreater(len(base.trades), 0)
        self.assertFalse(trained.trades)
        self.assertTrue(any(e["reason"] == "QUALITY_FILTER" for e in trained.events))

    def test_data_fault_prevents_reentry_and_is_excluded_from_quality_training(self):
        rows = [
            t
            for t in read_ticks(self.ticks)
            if not self.session.at(10, 1) <= t.timestamp < self.session.at(10, 4)
        ]
        engine = self.engine(ticks=rows)
        self.assertEqual(len(engine.trades), 1)
        self.assertEqual(engine.trades[0].exit_reason, "FAULT_EXIT")
        self.assertFalse(engine.learner.samples)
        self.assertTrue(engine.blocked)
        self.assertIn("QUALITY_SAMPLE_EXCLUDED", {e["reason"] for e in engine.events})

    def test_identical_ticks_and_learning_produce_identical_replay(self):
        ticks, calendar = generate_rolling(self.root / "longer", 24)
        a = replay(ticks, calendar, Config(strategy="R1"))
        b = replay(ticks, calendar, Config(strategy="R1"))
        self.assertGreater(len(a.trades), 5)
        self.assertEqual(a.trades, b.trades)
        self.assertEqual(a.event_hash(), b.event_hash())
        self.assertEqual(a.learner.samples, b.learner.samples)

    def test_r1_cannot_silently_enter_baseline_research_family(self):
        with self.assertRaisesRegex(ValueError, "paper experiment"):
            prepare(
                self.ticks, self.path, Config(strategy="R1"), self.root / "research"
            )

    def test_native_live_marks_and_loss_notifications_do_not_create_fake_exits(self):
        ledger = self.root / "Sim101_MNQ_R1_events.csv"
        with ledger.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "sequence",
                    "timestamp",
                    "account",
                    "stage",
                    "arm",
                    "reason",
                    "details",
                ]
            )

            def row(n, reason, detail):
                w.writerow(
                    [
                        n,
                        self.session.at(10, n).isoformat(),
                        "Sim101",
                        "Funded",
                        "R1",
                        reason,
                        detail,
                    ]
                )

            row(
                1,
                "FILLED",
                "name=MNQ_ENTRY;order_id=a;quantity=1;price=20000;direction=1",
            )
            row(
                2,
                "LIVE_STATUS",
                "open_qty=1;price=19999;unrealized_usd=-3;day_net_usd=0;blocked=False;news_pause=False",
            )
            row(3, "EMERGENCY_STOP", "net_usd=-5;fees=1")
            row(4, "LOSS_RECORDED", "trade_id=Sim101:a;net_usd=-5;questions=30")
            row(
                5,
                "ADAPTATION_UPDATE",
                "direction=1;observations=1;recent_net_usd=-5;min_efficiency=0.4;tightened=False",
            )
            row(
                6,
                "FILLED",
                "name=MNQ_ENTRY;order_id=b;quantity=1;price=20002;direction=1",
            )
            row(
                7,
                "LIVE_STATUS",
                "open_qty=1;price=20003;unrealized_usd=1;day_net_usd=-5;blocked=False;news_pause=False",
            )
        payload = native_payload([ledger])
        self.assertEqual(len(payload["trades"]), 1)
        self.assertEqual(len(payload["pending_positions"]), 1)
        self.assertEqual(payload["pending_positions"][0]["order_id"], "b")
        self.assertEqual(payload["live_status"][0]["unrealized_usd"], "1")
        self.assertEqual(payload["learning_status"][0]["observations"], "1")
        output = self.root / "dashboard.html"
        render(payload, output)
        self.assertIn("Current market and positions", output.read_text())
        self.assertIn("Quality filter learning", output.read_text())


if __name__ == "__main__":
    unittest.main()
