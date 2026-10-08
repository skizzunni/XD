import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.analyze_pine_audit import analyze, parse_audits


def log_line(trade, entry, exit_price, direction, entry_time, exit_time, **changes):
    # Independent MNQ accounting, with explicit synthetic stop/risk evidence.
    initial_stop = entry - direction * 30
    net = direction * (exit_price - entry) * 2 - 1.5
    fields = dict(variant="Baseline R2", trade=trade, profile="OVERNIGHT", quantity=1,
                  direction=direction, entry=entry, exit=exit_price,
                  initial_stop=initial_stop, final_stop=initial_stop,
                  initial_risk_usd=60, planned_risk_usd=64, allowance_usd=100,
                  net_usd=net, commission_usd=1.5, mfe_emulator_usd=8,
                  mae_emulator_usd=max(0, -net), entry_et=entry_time, exit_et=exit_time,
                  exit_reason="SYNTHETIC_EXIT", learning_eligible="true", chart_session="extended")
    fields.update(changes)
    return "[timestamp] R2_TRADE_AUDIT|" + "|".join(f"{k}={v}" for k, v in fields.items())


class PineAuditTests(unittest.TestCase):
    def samples(self):
        return [log_line(1, 20000, 19998.25, -1, "2026-01-05 06:00", "2026-01-05 06:45"),
                log_line(2, 20000, 20024.5, -1, "2026-01-05 09:00", "2026-01-05 09:10"),
                log_line(3, 20000, 19977.5, 1, "2026-01-05 14:00", "2026-01-05 14:05", profile="RTH")]

    def test_price_fee_reconciliation_and_loss_summary(self):
        result = analyze("\n".join(self.samples()))
        summary = result["experiments"][0]["summary"]
        self.assertEqual((summary["net_usd"], summary["winners"], summary["losers"]), (-95, 1, 2))
        self.assertEqual(summary["closed_trade_drawdown_usd"], 97)
        self.assertEqual(summary["mean_win_usd"], 2)
        self.assertEqual(summary["mean_loss_usd"], 48.5)
        self.assertAlmostEqual(summary["net_profit_factor"], 2/97)
        self.assertEqual(set(result["experiments"][0]["profiles"]), {"RTH", "OVERNIGHT"})

    def test_exact_duplicates_are_not_extra_trades(self):
        result = analyze("\n".join(self.samples()*2))
        self.assertEqual(result["experiments"][0]["summary"]["completed_positions"], 3)

    def test_conflicting_duplicates_fail(self):
        with self.assertRaisesRegex(ValueError, "Conflicting repeated"):
            analyze(self.samples()[0] + "\n" + self.samples()[0].replace("mfe_emulator_usd=8", "mfe_emulator_usd=9"))
        with self.assertRaisesRegex(ValueError, "Conflicting repeated"):
            analyze(self.samples()[0] + "\n" + self.samples()[0].replace("2026-01-05 06:45", "2026-01-05 06:50"))

    def test_sessions_and_settings_are_separate_experiments(self):
        log = self.samples()[0]
        for changed in (log.replace("chart_session=extended", "chart_session=regular"),
                        log + "|evaluation_target_usd=20",
                        log + "|entry_chase_ticks=8"):
            with self.subTest(changed=changed):
                result = analyze(log + "\n" + changed)
                self.assertEqual(len(result["experiments"]), 2)

    def test_net_and_stop_risk_mismatches_fail(self):
        for old, new, message in (("net_usd=2.0", "net_usd=200.0", "MNQ"), ("initial_risk_usd=60", "initial_risk_usd=6", "Initial stop"), ("allowance_usd=100", "allowance_usd=10", "Plan exceeded"), ("quantity=1", "quantity=nan", "Nonfinite")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                parse_audits(self.samples()[0].replace(old, new))

    def test_partial_or_non_audit_logs_are_rejected(self):
        for text in ("nothing", "R2_ENTRY_PLAN|quantity=1", "R2_TRADE_AUDIT|trade=1", self.samples()[0] + "|quantity=1"):
            with self.assertRaises(ValueError):
                parse_audits(text)

    def test_cli_preserves_existing_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            log, out = Path(directory)/"pine.txt", Path(directory)/"report.json"
            log.write_text("\n".join(self.samples()))
            command = [sys.executable, "-m", "scripts.analyze_pine_audit", "--log", str(log), "--out", str(out)]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            original = out.read_bytes()
            self.assertEqual(json.loads(original)["experiments"][0]["summary"]["net_usd"], -95)
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(out.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
