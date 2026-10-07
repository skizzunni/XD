import csv
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from bot.dashboard import native_payload, run_payload, html_document
from bot.fullsession import FullSessionEngine
from bot.models import Config
from bot.report import engine_report, write_run
from bot.review import diagnose
from bot.sizing import AdaptiveSizer, adaptive_state, budget_quantity
from tests.test_fullsession import fixture, ticks_for


def outcomes(values, eligible=True):
    return [{"net_r": v, "eligible": eligible} for v in values]


class AdaptiveSizingTests(unittest.TestCase):
    def test_can_grow_from_two_to_ten_in_evidence_blocks(self):
        for n, expected in ((0, 2), (19, 2), (20, 3), (39, 3), (40, 4), (160, 10), (200, 10)):
            self.assertEqual(adaptive_state(outcomes([1.] * n))["suggested_contracts"], expected)

    def test_one_large_winner_does_not_earn_a_promotion(self):
        samples = outcomes([-.2, .05] * 9 + [0., 12.])
        state = adaptive_state(samples)
        self.assertGreater(state["recent_net_r"], 4)
        self.assertEqual(state["suggested_contracts"], 2)
        self.assertFalse(state["growth_eligible"])

    def test_loss_streak_halves_size_and_never_increases_after_a_loss(self):
        samples = outcomes([1.] * 80)
        self.assertEqual(adaptive_state(samples)["suggested_contracts"], 6)
        self.assertEqual(adaptive_state(samples + outcomes([-1.]))["suggested_contracts"], 6)
        state = adaptive_state(samples + outcomes([-1., -1.]))
        self.assertEqual(state["suggested_contracts"], 3)
        self.assertEqual(state["reason"], "LOSS_STREAK_REDUCTION")
        self.assertEqual(adaptive_state(samples + outcomes([-1.] * 6))["suggested_contracts"], 1)

    def test_faults_reduce_size_and_cannot_earn_growth(self):
        state = adaptive_state(outcomes([1.] * 40) + outcomes([20.], False))
        self.assertEqual(state["suggested_contracts"], 2)
        self.assertEqual(state["observations"], 40)
        self.assertFalse(state["growth_eligible"])
        self.assertEqual(state["reason"], "FAULT_REDUCTION")

    def test_weak_window_reduces_size_even_without_consecutive_losses(self):
        state = adaptive_state(outcomes([1.] * 80 + [-1., .05] * 10))
        self.assertLess(state["suggested_contracts"], 6)
        self.assertLess(state["recent_net_r"], 0)

    def test_normalization_is_independent_of_quantity_and_uses_original_risk(self):
        stamp = datetime.fromisoformat("2026-10-07T10:00:00-04:00")
        for quantity in (1, 2, 5, 10):
            sizer = AdaptiveSizer()
            for i in range(20):
                sizer.record(str(i), stamp + timedelta(seconds=i), "TrendRunner:RTH",
                             12 * quantity, 10 * quantity)
            state = sizer.state("TrendRunner:RTH", stamp + timedelta(minutes=1))
            self.assertAlmostEqual(state["recent_net_r"], 24)
            self.assertEqual(state["suggested_contracts"], 3)

    def test_future_same_timestamp_wrong_profile_and_duplicates(self):
        stamp = datetime.fromisoformat("2026-10-07T10:00:00-04:00")
        sizer = AdaptiveSizer()
        for i in range(20):
            sizer.record(str(i), stamp, "TrendRunner:RTH", 10, 10)
        self.assertEqual(sizer.state("TrendRunner:RTH", stamp)["observations"], 0)
        self.assertEqual(sizer.state("Fixed:RTH", stamp + timedelta(seconds=1))["observations"], 0)
        self.assertEqual(sizer.state("TrendRunner:OVERNIGHT", stamp + timedelta(seconds=1))["observations"], 0)
        self.assertEqual(sizer.state("TrendRunner:RTH", stamp + timedelta(seconds=1))["suggested_contracts"], 3)
        with self.assertRaises(ValueError): sizer.record("0", stamp, "TrendRunner:RTH", 1, 1)
        for risk in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError): sizer.record("bad", stamp, "TrendRunner:RTH", 1, risk)

    def test_budget_fit_can_reduce_to_one_or_reject_without_expanding_budget(self):
        self.assertEqual(budget_quantity(10, 84.5, 100), 1)
        self.assertEqual(budget_quantity(10, 84.5, 26), 0)
        self.assertEqual(budget_quantity(10, 10, 100), 10)
        self.assertEqual(budget_quantity(3, 10, 100), 3)
        self.assertEqual(budget_quantity(3, 10, 29.999999999), 2)
        self.assertEqual(budget_quantity(3, 10, -1), 0)
        for risk in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError): budget_quantity(2, risk, 100)

    def test_r2_only_config_and_bounded_maximum(self):
        for arm in ("P0", "C1", "R1"):
            with self.assertRaises(ValueError): Config(strategy=arm, adaptive_sizing=True)
        for limit in (0, 11, True, 2.5):
            with self.assertRaises(ValueError): Config(strategy="R2", max_paper_contracts=limit)
        with self.assertRaises(ValueError): Config(strategy="R2", adaptive_sizing=1)
        with self.assertRaises(ValueError): Config(strategy="R2", adaptive_sizing=True, paper_contracts=5, max_paper_contracts=3)

    def test_actual_entry_uses_learned_quantity_and_completed_cash_is_consistent(self):
        with tempfile.TemporaryDirectory() as directory:
            calendar = fixture(Path(directory) / "calendar.json", 1)
            session = next(iter(calendar.sessions.values()))
            cfg = Config(strategy="R2", adaptive_sizing=True, paper_contracts=2,
                         adaptive_quality=False, session_loss_budget_usd=10000)
            engine = FullSessionEngine(cfg, calendar)
            engine.daily_ranges = [150] * 20
            for i in range(40):
                engine.sizer.record("prior-" + str(i), session.open - timedelta(seconds=50-i),
                                    "Fixed:OVERNIGHT", 10, 10)
            for tick in ticks_for(session): engine.process(tick)
            engine.finish()
            self.assertGreater(len(engine.trades), 0)
            self.assertEqual(engine.trades[0].quantity, 4)
            for trade, audit in zip(engine.trades, engine.trade_audits):
                self.assertEqual(audit["position_quantity"], trade.quantity)
                self.assertLessEqual(trade.quantity, cfg.max_paper_contracts)
                risk = (abs(trade.entry_fill-audit["initial_stop"])*2
                        + cfg.round_turn_fees_usd + .5) * trade.quantity
                self.assertLessEqual(risk, 10000 + min(0, audit["day_realized_before"]))
            self.assertAlmostEqual(engine.day_realized, sum(t.net_ticks*.5*t.quantity for t in engine.trades))
            self.assertEqual(len(engine.sizer.samples), 40 + len(engine.trades))
            trade, audit = engine.trades[0], engine.trade_audits[0]
            review = diagnose(trade, audit, engine.events, cfg)
            quantity_check = next(q for q in review["diagnostics"] if q["question"] == "Was quantity within the selected paper-contract setting?")
            self.assertEqual(quantity_check["status"], "pass")
            out = Path(directory) / "run"
            write_run(engine, out, {"config": cfg.to_dict()})
            payload = run_payload(out)
            self.assertEqual(len(payload["trades"]), len(engine.trades))
            self.assertTrue(payload["sizing_status"])
            self.assertIn("Adaptive paper sizing", html_document(payload))
            self.assertTrue(engine_report(engine, True)["adaptive_sizing"])

    def test_fixed_mode_preserves_requested_quantity(self):
        with tempfile.TemporaryDirectory() as directory:
            calendar = fixture(Path(directory) / "calendar.json", 1)
            cfg = Config(strategy="R2", paper_contracts=2, session_loss_budget_usd=10000)
            engine = FullSessionEngine(cfg, calendar); engine.daily_ranges = [150] * 20
            for tick in ticks_for(next(iter(calendar.sessions.values()))): engine.process(tick)
            engine.finish()
            self.assertTrue(engine.trades)
            self.assertEqual({t.quantity for t in engine.trades}, {2})
            self.assertFalse(any(e["reason"].startswith("SIZING_") for e in engine.events))

    def test_native_sizing_events_do_not_become_fake_closed_trades(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "events.csv"
            with ledger.open("w", newline="") as f:
                w = csv.writer(f); w.writerow(["sequence", "timestamp", "account", "stage", "arm", "reason", "details"])
                for i, reason in enumerate(("SIZING_UPDATE", "SIZING_DECISION")):
                    w.writerow([i, "2026-10-07T10:00:00-04:00", "Sim101", "Funded", "R2", reason,
                                "regime=RTH;exit_profile=TrendRunner;suggested_contracts=3;observations=20;recent_net_r=10;recent_drawdown_r=1;starting_contracts=2;maximum_contracts=10;selected_contracts=2;remaining_budget_usd=100;reason=PROFITABLE_BLOCK_INCREASE"])
            payload = native_payload([ledger])
            self.assertEqual(payload["trades"], [])
            self.assertEqual(payload["pending_positions"], [])
            self.assertEqual(len(payload["sizing_status"]), 1)
            self.assertEqual(payload["sizing_status"][0]["selected_contracts"], "2")
