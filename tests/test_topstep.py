from datetime import datetime
from dataclasses import fields
import json
import tempfile
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

from bot.manual_trades import read_topstep_positions, summarize_manual_trades
from bot.topstep_collector import CompletedBarCollector
from bot.topstep_gateway import AmbiguousOrderError, TopstepAPIError, TopstepGateway
from bot.topstep_risk import TopstepRiskConfig, TopstepRiskState, decide_entry


ET = ZoneInfo("America/New_York")


class TopstepRiskTests(unittest.TestCase):
    def state(self, **changes):
        values = dict(timestamp=datetime(2026, 1, 6, 10, tzinfo=ET), balance=100_000,
                      unrealized_pnl=0, session_net_pnl=0, mll_floor=97_000)
        values.update(changes)
        return TopstepRiskState(**values)

    def test_size_fits_stop_costs_daily_and_mll_buffers(self):
        config = TopstepRiskConfig()
        decision = decide_entry(config, self.state(), 40, 20, 100)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.quantity, 3)
        self.assertAlmostEqual(decision.per_micro_reservation_usd, 22.22)
        self.assertLessEqual(decision.position_reservation_usd, 75)
        self.assertEqual(config.firm_max_micros, 100)

    def test_profit_never_enlarges_risk_and_losses_reduce_it(self):
        config = TopstepRiskConfig()
        base = decide_entry(config, self.state(), 20, 20, 100)
        profit = decide_entry(config, self.state(session_net_pnl=500), 20, 20, 100)
        loss = decide_entry(config, self.state(session_net_pnl=-280), 20, 20, 100)
        self.assertEqual(base.quantity, profit.quantity)
        self.assertLess(loss.quantity, base.quantity)

    def test_fail_closed_gates(self):
        config = TopstepRiskConfig()
        cases = {
            "STALE_QUOTE": dict(quote_age_seconds=3),
            "HIGH_IMPACT_NEWS_BUFFER": dict(news_blocked=True),
            "POSITION_OR_ENTRY_ALREADY_ACTIVE": dict(open_micros=1),
            "LOSS_STREAK_LOCK": dict(consecutive_losses=3),
            "MLL_SAFETY_BUFFER": dict(balance=97_700),
            "SESSION_LOSS_LOCK": dict(session_net_pnl=-300),
            "SESSION_PROFIT_LOCK": dict(session_net_pnl=1000),
        }
        for reason, changes in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(decide_entry(config, self.state(**changes), 20, 2, 100).reason, reason)

    def test_topstep_closed_window_and_live_stage(self):
        config = TopstepRiskConfig()
        state = self.state(timestamp=datetime(2026, 1, 6, 16, 30, tzinfo=ET))
        self.assertEqual(decide_entry(config, state, 20, 1, 100).reason,
                         "OUTSIDE_TOPSTEP_ENTRY_HOURS")
        with self.assertRaisesRegex(ValueError, "Live Funded"):
            TopstepRiskConfig(stage="live_funded")

    def test_shipped_profile_is_safe_and_loadable(self):
        raw = json.loads((Path(__file__).parents[1]/"config.topstep-100k.json").read_text())
        names = {field.name for field in fields(TopstepRiskConfig)}
        config = TopstepRiskConfig(**{key: value for key, value in raw.items() if key in names})
        self.assertEqual(config.stage, "practice")
        self.assertFalse(raw["execution_enabled"])
        self.assertTrue(raw["require_server_stop_bracket"])
        self.assertTrue(raw["require_auto_oco_brackets"])


class GatewayTests(unittest.TestCase):
    def test_auth_and_read_calls_check_success(self):
        calls = []
        def transport(path, payload, token):
            calls.append((path, payload, token))
            if path.endswith("loginKey"):
                return {"success": True, "errorCode": 0, "token": "session"}
            return {"success": True, "errorCode": 0, "accounts": [{"id": 1}]}
        client = TopstepGateway(transport=transport)
        client.login("user", "secret")
        self.assertEqual(client.accounts(), [{"id": 1}])
        self.assertEqual(calls[-1][2], "session")

    def test_order_writes_default_off_and_live_refused(self):
        client = TopstepGateway(token="session", transport=lambda *args: {})
        kwargs = dict(stage="practice", account_id=1, contract_id="MNQ", side=0,
                      size=1, stop_ticks=20, target_ticks=40, custom_tag="unique")
        with self.assertRaisesRegex(TopstepAPIError, "disabled"):
            client.place_bracketed_market_entry(**kwargs)
        client.execution_enabled = True
        with self.assertRaisesRegex(TopstepAPIError, "Practice"):
            client.place_bracketed_market_entry(**{**kwargs, "stage": "live_funded"})

    def test_rejected_order_is_ambiguous_and_never_retried(self):
        calls = []
        def transport(*args):
            calls.append(args)
            return {"success": False, "errorCode": 2,
                    "errorMessage": "Bracket mode mismatch", "orderId": 99}
        client = TopstepGateway(token="session", execution_enabled=True, transport=transport)
        with self.assertRaises(AmbiguousOrderError) as caught:
            client.place_bracketed_market_entry(stage="practice", account_id=1,
                contract_id="MNQ", side=0, size=1, stop_ticks=20,
                target_ticks=40, custom_tag="unique")
        self.assertEqual(caught.exception.order_id, 99)
        self.assertEqual(len(calls), 1)

    def test_bracket_requires_two_to_one_reward_risk(self):
        client = TopstepGateway(token="session", execution_enabled=True,
                                transport=lambda *args: {})
        with self.assertRaisesRegex(ValueError, "twice"):
            client.place_bracketed_market_entry(stage="practice", account_id=1,
                contract_id="MNQ", side=0, size=1, stop_ticks=20,
                target_ticks=39, custom_tag="unique")


class ManualTradeTests(unittest.TestCase):
    def test_export_reconciles_prices_costs_and_summary(self):
        rows = """ID,Size,Entry Time,Exit Time,Entry Price,Exit Price,P&L,Commissions,Fees,Direction
1,2,2026-01-06T10:00:00-05:00,2026-01-06T10:05:00-05:00,20000,20010,$40,$1,$2,Long
2,2,2026-01-06T11:00:00-05:00,2026-01-06T11:10:00-05:00,20010,20020,-$40,$1,$2,Short
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"positions.csv"
            path.write_text(rows)
            result = summarize_manual_trades(read_topstep_positions(path))
        self.assertEqual(result["gross_pnl_usd"], 0)
        self.assertEqual(result["costs_usd"], 6)
        self.assertEqual(result["net_pnl_usd"], -6)
        self.assertAlmostEqual(result["win_rate"], .5)


class CollectorTests(unittest.TestCase):
    def test_capture_is_append_only_and_deduplicated(self):
        class Gateway:
            def bars(self, *args, **kwargs):
                return [
                    {"t": "2026-01-06T15:00:00+00:00", "o": 20000,
                     "h": 20001, "l": 19999, "c": 20000.25, "v": 100},
                    {"t": "2026-01-06T15:01:00+00:00", "o": 20000.25,
                     "h": 20002, "l": 20000, "c": 20001.75, "v": 120},
                ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"bars.jsonl"
            collector = CompletedBarCollector(Gateway(), "MNQ", path)
            now = datetime(2026, 1, 6, 16, tzinfo=ZoneInfo("UTC"))
            self.assertEqual(collector.capture(now), 2)
            self.assertEqual(collector.capture(now), 0)
            self.assertEqual(len(path.read_text().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
