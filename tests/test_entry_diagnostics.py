import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from bot.dashboard import engine_learning_events, feed_timing, native_payload
from windows_setup import diagnostic_report


class EntryDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def ledger(self, name, rows, arm="R1"):
        path = self.root / name
        with path.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["sequence", "timestamp", "account", "stage", "arm", "reason", "details"])
            for index, (timestamp, reason, detail) in enumerate(rows, 1):
                writer.writerow([index, timestamp, "Sim101", "Funded", arm, reason, detail])
        return path

    def test_block_evidence_survives_1500_routine_tick_updates(self):
        start = datetime(2026, 10, 7, 9, 30, tzinfo=timezone(timedelta(hours=-4)))
        rows = [(start.isoformat(), "ATR_WARMUP", "Load matching tick history; ATR20=0")]
        rows += [((start + timedelta(seconds=5 * index)).isoformat(), "LIVE_STATUS", "price=31300;open_qty=0;blocked=True") for index in range(1, 1501)]
        payload = native_payload([self.ledger("P0_events.csv", rows, arm="P0")])
        check = payload["entry_checks"][0]
        self.assertTrue(check["blocked"])
        self.assertEqual(check["recorded_checks"][0]["reason"], "ATR_WARMUP")
        self.assertEqual(check["recorded_checks"][0]["details"], rows[0][2])
        self.assertFalse(any(event["reason"] == "ATR_WARMUP" for event in payload["events"][-300:]))
        report = diagnostic_report(payload)
        self.assertEqual(report["total_event_count"], 1501)
        self.assertEqual([event["reason"] for event in report["diagnostic_events"]], ["ATR_WARMUP"])

    def test_prior_day_warmup_is_not_assigned_to_ready_current_session(self):
        path = self.ledger("R1_events.csv", [
            ("2026-10-06T09:30:00-04:00", "ATR_WARMUP", "ATR20=0"),
            ("2026-10-07T09:30:00-04:00", "ELIGIBLE", "atr20=100"),
            ("2026-10-07T10:05:00-04:00", "LIVE_STATUS", "blocked=False;price=31300"),
        ])
        check = native_payload([path])["entry_checks"][0]
        self.assertFalse(check["blocked"])
        self.assertEqual(check["logged_session"], "2026-10-07")
        self.assertEqual(check["recorded_checks"], [])

    def test_blocked_flag_without_evidence_keeps_cause_unknown(self):
        path = self.ledger("R1_events.csv", [
            ("2026-10-07T10:05:00-04:00", "LIVE_STATUS", "blocked=True;price=31300"),
        ])
        check = native_payload([path])["entry_checks"][0]
        self.assertTrue(check["blocked"])
        self.assertEqual(check["recorded_checks"], [])

    def test_routine_r1_risk_rejection_is_not_reported_as_a_session_block(self):
        path = self.ledger("R1_events.csv", [
            ("2026-10-07T10:05:00-04:00", "REJECTED", "INITIAL_RISK_OR_LOSS_BUDGET"),
            ("2026-10-07T10:05:05-04:00", "LIVE_STATUS", "blocked=False;price=31300"),
        ])
        check = native_payload([path])["entry_checks"][0]
        self.assertFalse(check["blocked"])
        self.assertEqual(check["recorded_checks"], [])

    def test_native_order_failure_is_reported_with_its_actual_details(self):
        path = self.ledger("R1_events.csv", [
            ("2026-10-07T10:05:00-04:00", "REJECTED", "name=MNQ_ENTRY;state=Rejected;error=NoError;native=order rejected"),
            ("2026-10-07T10:05:05-04:00", "LIVE_STATUS", "blocked=True;price=31300"),
        ])
        flag = native_payload([path])["entry_checks"][0]["recorded_checks"][0]
        self.assertEqual(flag["reason"], "REJECTED")
        self.assertIn("state=Rejected", flag["details"])

    def test_p0_fault_is_not_assigned_to_a_separate_r1_ledger(self):
        p0 = self.ledger("P0_events.csv", [
            ("2026-10-07T09:30:00-04:00", "UNRESOLVED_CONTRACT", "contract=MNQ DEC26"),
            ("2026-10-07T11:20:00-04:00", "LIVE_STATUS", "blocked=True;price=31300"),
        ], arm="P0")
        r1 = self.ledger("R1_events.csv", [
            ("2026-10-07T11:21:00-04:00", "LIVE_STATUS", "blocked=False;price=31301"),
        ])
        checks = {check["strategy"]: check for check in native_payload([p0, r1])["entry_checks"]}
        self.assertEqual(checks["P0"]["recorded_checks"][0]["reason"], "UNRESOLVED_CONTRACT")
        self.assertEqual(checks["R1"]["recorded_checks"], [])
        self.assertFalse(checks["R1"]["blocked"])

    def test_prior_day_live_flag_does_not_claim_state_for_new_session(self):
        path = self.ledger("R1_events.csv", [
            ("2026-10-06T15:00:00-04:00", "LIVE_STATUS", "blocked=True;price=31300"),
            ("2026-10-07T06:47:00-04:00", "CONFIGURATION", "arm=R1"),
        ])
        self.assertIsNone(native_payload([path])["entry_checks"][0]["blocked"])

    def test_r2_native_ledger_retains_both_profiles_and_overnight_futures_day(self):
        path = self.ledger("R2_events.csv", [
            ("2026-10-11T23:50:00-04:00", "ADAPTATION_UPDATE", "direction=1;regime=RTH;observations=8;recent_net_usd=10;min_efficiency=0.4"),
            ("2026-10-11T23:50:01-04:00", "ADAPTATION_UPDATE", "direction=1;regime=OVERNIGHT;observations=8;recent_net_usd=-10;min_efficiency=0.7"),
            ("2026-10-11T23:55:00-04:00", "FILLED", "name=MNQ_ENTRY;price=20000;quantity=1;direction=1;order_id=night-1;futures_day=2026-10-12;regime=OVERNIGHT"),
            ("2026-10-12T00:05:00-04:00", "STOP_EXIT", "net_usd=-10;fees=1"),
        ], arm="R2")
        payload = native_payload([path])
        self.assertEqual({s["regime"] for s in payload["learning_status"]}, {"RTH", "OVERNIGHT"})
        self.assertEqual(payload["trades"][0]["session"], "2026-10-12")
        self.assertEqual(payload["trades"][0]["regime"], "OVERNIGHT")
        self.assertEqual(payload["pending_positions"], [])

    def test_replay_dashboard_retains_latest_learning_per_direction_and_profile(self):
        rows = [
            {"reason":"ADAPTATION_UPDATE", "direction":1, "regime":"RTH", "observations":1},
            {"reason":"ADAPTATION_UPDATE", "direction":1, "regime":"OVERNIGHT", "observations":2},
            {"reason":"ADAPTATION_UPDATE", "direction":1, "regime":"RTH", "observations":3},
        ]
        (self.root/"events.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
        latest = {r["regime"]:r["observations"] for r in engine_learning_events(self.root)}
        self.assertEqual(latest, {"RTH":3, "OVERNIGHT":2})


class FeedTimingTests(unittest.TestCase):
    def test_confirmed_report_timing_is_old_without_claiming_connection_state(self):
        timing = feed_timing({
            "account": "Sim101", "timestamp": "2026-10-07T14:16:05.9770000-04:00",
            "details": "blocked=True",
        }, datetime.fromisoformat("2026-10-07T18:26:08.271174+00:00"))
        self.assertAlmostEqual(timing["tick_age_seconds"], 602.294174)
        self.assertEqual(timing["condition"], "OLD_TICK_TIMESTAMP")
        self.assertIsNone(timing["delivery_lag_seconds"])
        self.assertIn("cannot distinguish", timing["message"])

    def test_fresh_delivery_of_delayed_prices_is_not_labeled_a_current_feed(self):
        timing = feed_timing({
            "account": "Sim101", "timestamp": "2026-10-07T14:16:05-04:00",
            "details": "received_at_et=2026-10-07T14:26:05-04:00;max_tick_gap_seconds=90",
        }, datetime.fromisoformat("2026-10-07T18:26:08+00:00"))
        self.assertEqual(timing["condition"], "DELAYED_OR_CLOCK_OFFSET")
        self.assertEqual(timing["delivery_lag_seconds"], 600)
        self.assertEqual(timing["receipt_age_seconds"], 3)
        self.assertEqual(timing["limit_source"], "native")

    def test_actual_native_limit_and_clock_offset_are_reported(self):
        live = {"account": "Sim101", "timestamp": "2026-10-07T14:26:00-04:00",
                "details": "received_at_et=2026-10-07T14:26:00-04:00;max_tick_gap_seconds=1"}
        self.assertEqual(feed_timing(live, datetime.fromisoformat("2026-10-07T18:26:05+00:00"))["condition"], "OLD_TICK_TIMESTAMP")
        self.assertEqual(feed_timing(live, datetime.fromisoformat("2026-10-07T18:25:00+00:00"))["condition"], "CLOCK_AHEAD")

    def test_playback_and_invalid_timestamps_do_not_claim_live_feed_freshness(self):
        for live in (None, {"account": "Playback101", "timestamp": "2020-01-01T12:00:00-05:00"},
                     {"account": "Sim101", "timestamp": "bad"},
                     {"account": "Sim101", "timestamp": "2026-10-07T12:00:00"}):
            self.assertIsNone(feed_timing(live))
        live = {"account": "Sim101", "timestamp": "2026-10-07T14:26:00-04:00",
                "details": "received_at_et=bad;max_tick_gap_seconds=NaN"}
        result = feed_timing(live, datetime(2026, 10, 7, 18, 26, tzinfo=timezone.utc))
        self.assertEqual(result["condition"], "RECENT_TICK_TIMESTAMP")
        self.assertEqual(result["limit_source"], "dashboard_default")


if __name__ == "__main__":
    unittest.main()
