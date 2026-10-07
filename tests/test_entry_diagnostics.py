import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from bot.dashboard import native_payload
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


if __name__ == "__main__":
    unittest.main()
