import csv
import json
import random
import unittest

from bot.backtest import ReplayFailure, replay
from bot.broker import PaperBroker
from bot.dashboard import native_payload, render
from bot.models import Config
from bot.report import write_run
from tests import test_engine as fixtures


class StressTests(unittest.TestCase):
    setUp = fixtures.EngineTests.setUp
    tearDown = fixtures.EngineTests.tearDown
    tick = fixtures.EngineTests.tick
    engine = fixtures.EngineTests.engine
    drive = fixtures.EngineTests.drive

    def test_two_thousand_random_bid_ask_cost_invariants(self):
        rng = random.Random(1781)
        for i in range(2000):
            direction = rng.choice((-1, 1))
            spread = rng.randrange(0, 10)
            slip = rng.randrange(0, 5)
            fee = rng.randrange(0, 10) / 2
            cfg = Config(
                spread_ticks=rng.randrange(0, 20),
                slippage_ticks_per_side=slip,
                round_turn_fees_usd=fee,
            )
            broker = PaperBroker(cfg)
            ep = 20000 + rng.randrange(-100, 100) * 0.25
            xp = ep + rng.randrange(-100, 100) * 0.25
            broker.enter(
                self.tick(360, ep, bid=ep, ask=ep + spread * 0.25),
                direction,
                ep - direction * 30,
            )
            trade = broker.exit(
                self.tick(389, xp, bid=xp, ask=xp + spread * 0.25),
                self.session,
                "TIME_EXIT",
            )
            self.assertAlmostEqual(trade.execution_ticks, spread + 2 * slip)
            self.assertAlmostEqual(
                trade.net_ticks, trade.gross_ticks - spread - 2 * slip - fee / 0.5
            )
            self.assertIsNone(broker.position)

    def test_gap_through_break_even_reports_loss_and_budget_overrun(self):
        e = self.engine(Config(break_even_trigger_r=1))
        self.drive(e, 361)
        e.process(self.tick(361, 20040))
        e.process(self.tick(362, 19800))
        self.assertLess(e.trades[0].net_ticks * 0.5, -100)
        self.assertEqual(e.trades[0].exit_reason, "EMERGENCY_STOP")
        self.assertIn("LOSS_RECORDED", [x["reason"] for x in e.events])

    def test_stop_target_paths_on_same_five_minute_bar(self):
        for first in ("stop", "target"):
            e = self.engine(Config(strategy="C1"))
            from bot.strategy import FVG

            e.process(self.tick(0, 20000))
            e.setup = FVG(
                1,
                20003,
                19997.75,
                self.session.open,
                self.session.open.replace(minute=59),
                2,
            )
            e.setup_chosen = True
            e.process(self.tick(1, 20010))
            e.process(self.tick(2, 20002.75))
            p = e.broker.position
            self.assertIsNotNone(p)
            prices = [p.stop, p.target] if first == "stop" else [p.target, p.stop]
            e.process(self.tick(3, prices[0]))
            e.process(self.tick(3, prices[1], second=1, tick_id="second"))
            self.assertEqual(
                e.trades[0].exit_reason,
                "EMERGENCY_STOP" if first == "stop" else "TARGET_EXIT",
            )
            self.assertEqual(len(e.trades), 1)

    def test_first_tick_on_fvg_formation_boundary_can_fill(self):
        e = self.engine(Config(strategy="C1"))
        from bot.strategy import FVG

        e.process(self.tick(0, 20010))
        e.setup = FVG(
            1,
            20003,
            19997.75,
            self.session.open.replace(minute=31),
            self.session.open.replace(minute=59),
            2,
        )
        e.setup_chosen = True
        e.process(self.tick(1, 20002.75))
        self.assertIsNotNone(e.broker.position)

    def test_early_close_daily_range_contributes_to_next_atr(self):
        raw = json.loads(self.calendar_path.read_text())
        raw["sessions"][20]["close"] = self.session.at(13, 0).isoformat()
        p = self.root / "half.json"
        p.write_text(json.dumps(raw))
        from bot.data import Calendar
        from bot.engine import Engine

        e = Engine(Config(), Calendar(p))
        e.daily_ranges = [100] * 20
        self.drive(e, 210)
        e.finish()
        self.assertEqual(len(e.daily_ranges), 21)
        self.assertEqual(e.daily_ranges[-1], 10)

    def test_failure_artifacts_preserve_open_position(self):
        e = self.engine()
        self.drive(e, 361)
        path = self.root / "duplicate.csv"
        lines = self.ticks.read_text().splitlines()
        index = 1 + 20 * 390 + 362
        lines.insert(index, lines[index - 1])
        path.write_text("\n".join(lines) + "\n")
        with self.assertRaises(ReplayFailure) as caught:
            replay(path, self.calendar_path, Config())
        self.assertIn("DUPLICATE_TICK", str(caught.exception))
        write_run(caught.exception.engine, self.root / "failed", {})
        report = json.loads((self.root / "failed" / "report.json").read_text())
        self.assertFalse(report["completed"])
        self.assertIsNotNone(
            json.loads((self.root / "failed" / "open_position.json").read_text())
        )
        self.assertEqual(report["decision"], "FAILED_RUN_DO_NOT_USE_FOR_PERFORMANCE")

    def test_trade_path_counts_each_tick_once(self):
        e = self.engine()
        self.drive(e)
        e.finish()
        self.assertEqual(e.trade_audits[0]["observed_ticks_in_trade"], 30)

    def test_dashboard_real_native_ledger_matches_loss_review(self):
        events = self.root / "Sim101_MNQ_P0_events.csv"
        reviews = self.root / "Sim101_MNQ_P0_loss_reviews.csv"
        with events.open("w", newline="") as f:
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
            w.writerow(
                [
                    1,
                    "2026-10-07T15:30:00-04:00",
                    "Sim101",
                    "Funded",
                    "P0",
                    "FILLED",
                    "name=MNQ_ENTRY;order_id=abc;quantity=1;price=20000",
                ]
            )
            w.writerow(
                [
                    2,
                    "2026-10-07T15:59:00-04:00",
                    "Sim101",
                    "Funded",
                    "P0",
                    "TIME_EXIT",
                    "net_usd=-11;fees=1",
                ]
            )
        with reviews.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "trade_id",
                    "timestamp",
                    "account",
                    "stage",
                    "arm",
                    "question",
                    "answer",
                    "status",
                ]
            )
            for i in range(30):
                w.writerow(
                    [
                        "Sim101:abc",
                        "2026-10-07T15:59:00-04:00",
                        "Sim101",
                        "Funded",
                        "P0",
                        f"Question {i}",
                        "Unknown",
                        "unknown",
                    ]
                )
        payload = native_payload([events])
        self.assertEqual(payload["trades"][0]["net_usd"], -11)
        self.assertEqual(len(payload["loss_reviews"][0]["diagnostics"]), 30)
        self.assertFalse(payload["pending_positions"])
        output = self.root / "dashboard.html"
        render(payload, output)
        self.assertIn("MNQ trade dashboard", output.read_text())

    def test_html_payload_cannot_inject_script(self):
        p = self.root / "x.html"
        render({"source": "</script><script>alert(1)</script>"}, p)
        self.assertNotIn("</script><script>alert(1)</script>", p.read_text())
        self.assertIn("\\u003c", p.read_text())


if __name__ == "__main__":
    unittest.main()
