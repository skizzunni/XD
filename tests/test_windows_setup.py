from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bot.data import Calendar
import windows_setup


class WindowsSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "OneDrive/My Documents/NinjaTrader 8"
        self.strategies = self.home / "bin/Custom/Strategies"
        self.strategies.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_preserves_old_files_and_is_repeatable_from_any_directory(self):
        strategy = self.strategies / "MNQPlanPaper.cs"
        calendar = self.home / "MNQCalendar.csv"
        strategy.write_text("old strategy with personal edits")
        calendar.write_text("old calendar")
        with redirect_stdout(io.StringIO()):
            backups = windows_setup.install(self.home)
            again = windows_setup.install(self.home)
        self.assertEqual((backups / strategy.name).read_text(), "old strategy with personal edits")
        self.assertEqual((backups / calendar.name).read_text(), "old calendar")
        self.assertEqual(strategy.read_bytes(), (windows_setup.PROJECT / "ninjatrader/MNQPlanPaper.cs").read_bytes())
        self.assertEqual(calendar.read_bytes(), (windows_setup.SIM_PRESET / calendar.name).read_bytes())
        self.assertFalse(again.exists())

    def test_missing_ninjatrader_does_not_create_fake_installation(self):
        with self.assertRaisesRegex(ValueError, "Strategies folder missing"):
            windows_setup.install(self.root / "not-installed")
        self.assertFalse((self.root / "not-installed").exists())

    def test_calendar_body_tampering_is_rejected_before_any_install(self):
        preset = self.root / "calendar"
        preset.mkdir()
        for name in ("calendar.json", "MNQCalendar.csv"):
            (preset / name).write_bytes((windows_setup.SIM_PRESET / name).read_bytes())
        csv = preset / "MNQCalendar.csv"
        csv.write_bytes(csv.read_bytes().replace(b"MNQ 12-26", b"MNQ 09-26"))
        with self.assertRaisesRegex(ValueError, "differs from its frozen JSON"):
            windows_setup.install(self.home, preset)
        self.assertFalse((self.strategies / "MNQPlanPaper.cs").exists())
        self.assertFalse((self.home / "MNQCalendar.csv").exists())

    def test_failed_replace_keeps_previous_file_and_removes_temporary_source(self):
        strategy = self.strategies / "MNQPlanPaper.cs"
        strategy.write_text("previous source")
        with patch("windows_setup.os.replace", side_effect=OSError("locked by editor")):
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(OSError, "locked by editor"):
                windows_setup.install(self.home)
        self.assertEqual(strategy.read_text(), "previous source")
        self.assertFalse(list(self.strategies.glob("*.tmp")))
        self.assertEqual(len(list((self.home / "MNQPaper/install-backups").glob("*/MNQPlanPaper.cs"))), 1)

    def test_watcher_opens_local_dashboard_from_sim101_ledger(self):
        folder = self.home / "MNQPaper/p0-sim101-001"
        folder.mkdir(parents=True)
        ledger = folder / "Sim101_MNQ_12_26_P0_events.csv"
        ledger.write_text('sequence,timestamp,account,stage,arm,reason,details\n1,2026-10-07T09:30:00-04:00,Sim101,Funded,P0,ELIGIBLE,atr20=100\n')
        with patch("windows_setup.webbrowser.open") as opened, redirect_stdout(io.StringIO()):
            output = windows_setup.watch(self.home, "p0-sim101-001", once=True)
        self.assertTrue(output.is_file())
        self.assertIn("Sim101", output.read_text())
        opened.assert_called_once_with(output.resolve().as_uri())

    def test_watcher_waits_for_actual_logs_and_rejects_path_traversal(self):
        with patch("windows_setup.webbrowser.open") as opened, redirect_stdout(io.StringIO()):
            self.assertIsNone(windows_setup.watch(self.home, "p0-sim101-001", once=True))
        opened.assert_not_called()
        with self.assertRaisesRegex(ValueError, "Run ID"):
            windows_setup.watch(self.home, "../other", once=True)

    def test_sim101_preset_trades_current_dates_and_blocks_historical_entries(self):
        calendar = Calendar(windows_setup.SIM_PRESET / "calendar.json")
        active = [str(s.day) for s in calendar.sessions.values() if s.trade_enabled]
        self.assertEqual(active, ["2026-10-07", "2026-10-08", "2026-10-09"])
        self.assertEqual(len(calendar.sessions), 34)
        self.assertTrue(all(s.eligibility == "ELIGIBLE" for s in calendar.sessions.values() if s.trade_enabled))
        self.assertFalse(calendar.synthetic)


if __name__ == "__main__":
    unittest.main()
