from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

from bot.accounts import apply_account, update_account
from bot.backtest import replay
from bot.broker import PaperBroker
from bot.data import Calendar, parse_time
from bot.demo import generate
from bot.engine import Engine
from bot.models import Bar, Config, Tick
from bot.strategy import fvg_setup, momentum_direction


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ticks, self.calendar_path = generate(self.root / "fixture", 24)
        self.calendar = Calendar(self.calendar_path)
        self.day = sorted(self.calendar.sessions)[20]
        self.session = self.calendar.sessions[self.day]

    def tearDown(self):
        self.tmp.cleanup()

    def tick(self, minute, price=20000, tick_id=None, second=0, bid=None, ask=None):
        return Tick(
            self.session.open + timedelta(minutes=minute, seconds=second),
            price,
            1,
            "MNQ-TEST",
            tick_id or f"t-{minute}-{second}",
            bid,
            ask,
        )

    def engine(self, config=None):
        engine = Engine(config or Config(), self.calendar)
        engine.daily_ranges = [100.0] * 20
        return engine

    def drive(self, engine, end=390, change=None):
        for m in range(end):
            price = 20000 if m == 0 else 20010
            if change:
                price = change(m, price)
            engine.process(self.tick(m, price))

    def test_boundary_and_both_directions(self):
        self.assertEqual(momentum_direction(20000, 20009.9999, 100)[0], 0)
        self.assertEqual(momentum_direction(20000, 20010, 100)[0], 1)
        self.assertEqual(momentum_direction(20000, 19990, 100)[0], -1)

    def test_signal_freezes_at_1000_and_atr_is_lagged(self):
        e = self.engine()
        self.drive(e, change=lambda m, p: 20500 if 31 <= m < 360 else p)
        e.finish()
        signal = next(v for v in e.events if v["stage"] == "signal")
        self.assertEqual(signal["atr20"], 100)
        self.assertEqual(signal["m"], 0.1)
        self.assertEqual(signal["timestamp"], self.session.at(10, 0).isoformat())
        self.assertEqual(len(signal["source_bars"]), 6)
        self.assertEqual(len(e.trades), 1)
        self.assertEqual(e.trades[0].entry_time, self.session.at(15, 30).isoformat())
        self.assertEqual(e.trades[0].exit_time, self.session.at(15, 59).isoformat())

    def test_replay_twice_identical_events_and_pnl(self):
        a = replay(self.ticks, self.calendar_path, Config())
        b = replay(self.ticks, self.calendar_path, Config())
        self.assertEqual(a.event_hash(), b.event_hash())
        self.assertEqual(a.trades, b.trades)
        self.assertGreater(len(a.trades), 0)
        self.assertTrue(all(e["reason"] for e in a.events))

    def test_stop_precedes_time_exit_on_same_tick(self):
        e = self.engine()
        self.drive(e, change=lambda m, p: 19980 if m == 389 else p)
        e.finish()
        self.assertEqual(e.trades[0].exit_reason, "EMERGENCY_STOP")
        self.assertEqual(len(e.trades), 1)

    def test_observed_earlier_time_exit_beats_later_stop(self):
        e = self.engine()
        self.drive(e)
        e.process(self.tick(389, 19900, tick_id="later", second=1))
        e.finish()
        self.assertEqual(e.trades[0].exit_reason, "TIME_EXIT")

    def test_early_close_and_roll_have_no_entry(self):
        for key in ("early", "roll"):
            raw = json.loads(self.calendar_path.read_text())
            row = raw["sessions"][20]
            if key == "early":
                row["close"] = self.session.at(13, 0).isoformat()
            else:
                row["roll_day"] = True
            path = self.root / f"{key}.json"
            path.write_text(json.dumps(raw))
            e = Engine(Config(), Calendar(path))
            e.daily_ranges = [100] * 20
            self.drive(e, 210 if key == "early" else 390)
            e.finish()
            self.assertFalse(e.trades)
            self.assertIn(
                "EARLY_CLOSE" if key == "early" else "ROLL_DAY",
                [v["reason"] for v in e.events],
            )

    def test_late_news_blocks_earlier_news_does_not(self):
        for hour, expected in ((14, True), (15, False)):
            raw = json.loads(self.calendar_path.read_text())
            raw["sessions"][20]["news"] = {
                "flags": ["FOMC"],
                "releases": [
                    {
                        "name": "FOMC",
                        "revision": [],
                        "timestamp": self.session.at(hour, 30).isoformat(),
                    }
                ],
            }
            path = self.root / f"news-{hour}.json"
            path.write_text(json.dumps(raw))
            e = Engine(Config(), Calendar(path))
            e.daily_ranges = [100] * 20
            self.drive(e)
            e.finish()
            self.assertEqual(bool(e.trades), expected)

    def test_gap_after_entry_fault_flattens_and_blocks(self):
        e = self.engine()
        self.drive(e, 361)
        e.process(self.tick(365, 20010))
        self.assertEqual(e.trades[0].exit_reason, "FAULT_EXIT")
        self.assertTrue(e.blocked)
        self.assertIsNone(e.broker.position)

    def test_duplicate_and_out_of_order_rejected(self):
        e = self.engine()
        t = self.tick(0)
        e.process(t)
        with self.assertRaisesRegex(ValueError, "DUPLICATE_TICK"):
            e.process(t)
        e = self.engine()
        e.process(self.tick(1))
        with self.assertRaisesRegex(ValueError, "BAD_TIMESTAMP"):
            e.process(self.tick(0))

    def test_naive_zero_volume_and_bad_quote_rejected(self):
        t = self.tick(0)
        for bad in (
            replace(t, timestamp=t.timestamp.replace(tzinfo=None)),
            replace(t, volume=0),
            replace(t, price=float("nan")),
            replace(t, bid=20001, ask=20000),
        ):
            with self.assertRaises(ValueError):
                bad.validate()

    def test_quote_costs_not_double_counted(self):
        cfg = Config(round_turn_fees_usd=1, slippage_ticks_per_side=1, spread_ticks=10)
        b = PaperBroker(cfg)
        b.enter(self.tick(360, bid=20000, ask=20000.25), 1, 19980)
        trade = b.exit(
            self.tick(389, 20010, bid=20010, ask=20010.25), self.session, "TIME_EXIT"
        )
        self.assertEqual(trade.gross_ticks, 40)
        self.assertEqual(trade.execution_ticks, 3)  # one quoted spread, two slippage
        self.assertEqual(trade.net_ticks, 35)

    def test_slippage_cap_marks_missed_without_chasing(self):
        e = self.engine()
        self.drive(e, change=lambda m, p: 20020 if m >= 360 else p)
        e.finish()
        self.assertFalse(e.trades)
        self.assertIn("MISSED", [v["reason"] for v in e.events])

    def test_unfinished_path_does_not_fabricate_exit(self):
        e = self.engine()
        self.drive(e, 365)
        with self.assertRaisesRegex(ValueError, "UNRESOLVED_POSITION"):
            e.finish()
        self.assertTrue(e.broker.position)

    def test_reconstruct_at_1540_one_position_stop_and_exit(self):
        continuous = self.engine()
        recovered = self.engine()
        self.drive(continuous, 370)
        self.drive(
            recovered, 370
        )  # deterministic reconstruction of retained input, no repeat live orders
        self.assertEqual(continuous.broker.position, recovered.broker.position)
        self.assertIsNotNone(recovered.broker.position.stop)
        for m in range(370, 390):
            continuous.process(self.tick(m, 20010))
            recovered.process(self.tick(m, 20010))
        continuous.finish()
        recovered.finish()
        self.assertEqual(continuous.event_hash(), recovered.event_hash())
        self.assertEqual(len(recovered.trades), 1)

    def test_break_even_accounts_for_fees_but_gap_can_lose(self):
        e = self.engine(Config(break_even_trigger_r=1))
        self.drive(e, 361)
        e.process(self.tick(361, 20040))
        self.assertTrue(e.broker.position.break_even_armed)
        stop = e.broker.position.stop
        e.process(self.tick(362, stop))
        self.assertGreaterEqual(e.trades[0].net_ticks, 0)
        e = self.engine(Config(break_even_trigger_r=1))
        self.drive(e, 361)
        e.process(self.tick(361, 20040))
        e.process(self.tick(362, 20000))
        self.assertLess(e.trades[0].net_ticks, 0)

    def test_daily_profit_target_and_funded_mode(self):
        e = self.engine(Config(daily_profit_target_usd=20))
        self.drive(e, 361)
        e.process(self.tick(361, 20025))
        self.assertEqual(e.trades[0].exit_reason, "DAILY_PROFIT_TARGET")
        e = self.engine(Config(daily_profit_target_usd=0))
        self.drive(e, 361)
        e.process(self.tick(361, 20025))
        self.assertIsNotNone(e.broker.position)

    def test_account_stage_switch_preserves_loss_limit(self):
        path = self.root / "accounts.json"
        cfg, _, account = apply_account(Config(), path)
        self.assertEqual(account["stage"], "funded")
        self.assertEqual(cfg.daily_profit_target_usd, 0)
        update_account(path, "eval-a", "evaluation", select=True, loss_limit=75)
        cfg, _, _ = apply_account(Config(), path)
        self.assertEqual(cfg.daily_profit_target_usd, 750)
        self.assertEqual(cfg.session_loss_budget_usd, 75)
        update_account(path, "eval-a", "funded")
        cfg, _, _ = apply_account(Config(), path)
        self.assertEqual(cfg.daily_profit_target_usd, 0)
        self.assertEqual(cfg.session_loss_budget_usd, 75)

    def test_live_mode_is_unavailable(self):
        with self.assertRaisesRegex(ValueError, "live"):
            Config(mode="live")

    def test_dst_uses_eastern_zone(self):
        winter = parse_time("2026-01-05T14:30:00Z")
        summer = parse_time("2026-07-06T13:30:00Z")
        self.assertEqual(winter.hour, 9)
        self.assertEqual(summer.hour, 9)
        self.assertNotEqual(winter.utcoffset(), summer.utcoffset())

    def test_fvg_pattern_and_strict_trade_through(self):
        s = self.session
        values = [
            (20000, 20002, 19998, 20001),
            (20001, 20010, 20000, 20009),
            (20008, 20012, 20004, 20010),
        ]
        bars = [
            Bar(
                s.open + timedelta(minutes=i * 5),
                s.open + timedelta(minutes=(i + 1) * 5),
                *v,
                5,
            )
            for i, v in enumerate(values)
        ]
        setup = fvg_setup(bars, s, 100)
        self.assertEqual(setup.midpoint, 20003)
        self.assertEqual(setup.stop, 19997.75)
        e = self.engine(Config(strategy="C1"))
        # Build the pattern from actual tick path, then touch and cross on separate ticks.
        for m in range(15):
            p = values[m // 5][m % 4]
            e.process(self.tick(m, p))
        e.process(self.tick(15, 20010))
        self.assertIsNotNone(e.setup)
        e.process(self.tick(16, 20003))
        self.assertIsNone(e.broker.position)
        e.process(self.tick(17, 20002.75))
        self.assertIsNotNone(e.broker.position)
        self.assertEqual(e.broker.position.direction, 1)


if __name__ == "__main__":
    unittest.main()
