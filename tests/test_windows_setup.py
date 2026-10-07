from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

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
            output = windows_setup.watch(self.home, "p0-sim101-001", once=True)
        self.assertTrue(output.is_file())
        self.assertIn('"state": "waiting_for_logs"', output.read_text())
        opened.assert_called_once_with(output.resolve().as_uri())
        with self.assertRaisesRegex(ValueError, "Run ID"):
            windows_setup.watch(self.home, "../other", once=True)

    def test_incomplete_package_is_rejected_before_changing_installed_files(self):
        project = self.root / "incomplete-download"
        for name in ("requirements.txt", "main.py", "bot/data.py", "bot/dashboard.py"):
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
        installed = self.strategies / "MNQPlanPaper.cs"
        installed.write_text("preserve my installed source")
        with patch("windows_setup.PROJECT", project):
            with self.assertRaisesRegex(ValueError, "Incomplete extracted bot package.*Missing files"):
                windows_setup.install(self.home)
        self.assertEqual(installed.read_text(), "preserve my installed source")
        self.assertFalse((self.home / "MNQCalendar.csv").exists())

    def test_package_preflight_does_not_require_ninjatrader_or_timezone_dependencies(self):
        with patch("windows_setup.ninja_home") as locate, redirect_stdout(io.StringIO()):
            self.assertEqual(windows_setup.main(["check-package"]), 0)
        locate.assert_not_called()

    def test_launch_uses_the_selected_run_for_install_and_browser(self):
        with patch("builtins.input", return_value="custom-sim-004"), patch("windows_setup.install") as install, patch("windows_setup.watch") as watch, redirect_stdout(io.StringIO()):
            windows_setup.launch(self.home)
        install.assert_called_once_with(self.home, run_id="custom-sim-004")
        watch.assert_called_once_with(self.home, "custom-sim-004")

    def test_dashboard_only_launch_preserves_running_strategy_and_calendar(self):
        folder = self.home / "MNQPaper/forward-paper"
        folder.mkdir(parents=True)
        (folder / "Sim101_events.csv").write_text(
            'sequence,timestamp,account,stage,arm,reason,details\n'
            '1,2026-10-07T06:41:00-04:00,Sim101,Funded,R1,CONFIGURATION,arm=R1\n'
        )
        installed = self.strategies / "MNQPlanPaper.cs"
        installed.write_text("my running source")
        calendar = self.home / "MNQCalendar.csv"
        calendar.write_text("my reviewed calendar")
        with patch("builtins.input", return_value="") as answer, patch("windows_setup.install") as install, patch("windows_setup.watch") as watch, redirect_stdout(io.StringIO()):
            windows_setup.launch(self.home, dashboard_only=True)
        self.assertIn("[forward-paper]", answer.call_args.args[0])
        install.assert_not_called()
        watch.assert_called_once_with(self.home, "forward-paper")
        self.assertEqual(installed.read_text(), "my running source")
        self.assertEqual(calendar.read_text(), "my reviewed calendar")

    def test_invalid_launcher_run_is_rejected_before_install_or_browser(self):
        with patch("windows_setup.install") as install, patch("windows_setup.watch") as watch, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "Run ID"):
                windows_setup.launch(self.home, "../bad")
        install.assert_not_called()
        watch.assert_not_called()

    def test_malformed_existing_run_does_not_prevent_browser_startup(self):
        folder = self.home / "MNQPaper" / "broken-old-run"
        folder.mkdir(parents=True)
        (folder / "Sim101_events.csv").write_text('sequence,timestamp,account,stage,arm,reason,details\n1,"unclosed quotation\n')
        found = windows_setup.discover_runs(self.home)
        self.assertIn("Cannot read ledger", found[0]["reason"])
        with patch("windows_setup.install") as install, patch("windows_setup.watch") as watch, redirect_stdout(io.StringIO()):
            windows_setup.launch(self.home, "r1-sim101-001", dashboard_only=True)
        install.assert_not_called()
        watch.assert_called_once_with(self.home, "r1-sim101-001")

    def test_sim101_preset_trades_current_dates_and_blocks_historical_entries(self):
        calendar = Calendar(windows_setup.PROJECT / "ninjatrader/calendars/mnq-dec26-sim101-2026-10-07-09/calendar.json")
        active = [str(s.day) for s in calendar.sessions.values() if s.trade_enabled]
        self.assertEqual(active, ["2026-10-07", "2026-10-08", "2026-10-09"])
        self.assertEqual(len(calendar.sessions), 34)
        self.assertTrue(all(s.eligibility == "ELIGIBLE" for s in calendar.sessions.values() if s.trade_enabled))
        self.assertFalse(calendar.synthetic)


class BrowserDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "OneDrive/Documents/NinjaTrader 8"
        self.run_id = "r1-sim101-001"
        self.folder = self.home / "MNQPaper" / self.run_id
        self.server = windows_setup.create_dashboard_server(self.home, self.run_id, port=0)
        self.worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.worker.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/"

    def tearDown(self):
        self.server.shutdown()
        self.worker.join(timeout=2)
        self.server.server_close()
        self.tmp.cleanup()

    def get(self):
        with urlopen(self.url, timeout=3) as response:
            page = response.read().decode("utf-8")
            payload = json.loads(page.split('<script id="payload" type="application/json">', 1)[1].split('</script>', 1)[0])
            return page, payload, response.headers

    def test_browser_opens_waiting_page_without_creating_ninjatrader_files(self):
        page, payload, headers = self.get()
        self.assertEqual(payload["startup"]["state"], "waiting_for_logs")
        self.assertIn("Position status unavailable", page)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertFalse(self.home.exists())
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_browser_reads_new_ticks_without_replacing_locked_html(self):
        self.folder.mkdir(parents=True)
        output = self.folder / "dashboard.html"
        output.write_text("existing file held by browser or OneDrive")
        ledger = self.folder / "Sim101_R1_events.csv"
        ledger.write_text('sequence,timestamp,account,stage,arm,reason,details\n1,2026-10-07T06:41:00-04:00,Sim101,Funded,R1,CONFIGURATION,arm=R1\n')
        with patch("pathlib.Path.replace", side_effect=PermissionError("WinError 5")):
            _, initial, _ = self.get()
            self.assertEqual(initial["startup"]["state"], "logs_found")
            with ledger.open("a") as stream:
                stream.write('2,2026-10-07T06:41:05-04:00,Sim101,Funded,R1,LIVE_STATUS,price=20000;open_qty=0;unrealized_usd=0;day_net_usd=0;phase=OUTSIDE_RTH\n')
            _, updated, _ = self.get()
        self.assertEqual(updated["live_status"][0]["price"], "20000")
        self.assertEqual(updated["live_status"][0]["phase"], "OUTSIDE_RTH")
        self.assertEqual(output.read_text(), "existing file held by browser or OneDrive")
        self.assertFalse((self.folder / "dashboard.tmp.html").exists())

    def test_waiting_page_reports_other_run_instead_of_silently_switching(self):
        other = self.home / "MNQPaper/forward-paper"
        other.mkdir(parents=True)
        (other / "Sim101_P0_events.csv").write_text('sequence,timestamp,account,stage,arm,reason,details\n1,2026-10-07T06:41:00-04:00,Sim101,Funded,P0,ATR_WARMUP,history missing\n')
        _, payload, _ = self.get()
        self.assertEqual(payload["startup"]["run_id"], self.run_id)
        self.assertEqual(payload["startup"]["state"], "waiting_for_logs")
        found = payload["startup"]["other_runs"][0]
        self.assertEqual((found["run_id"], found["strategy"], found["reason"]), ("forward-paper", "P0", "ATR_WARMUP"))

    def test_log_read_failure_shows_retry_page_instead_of_failing_request(self):
        with patch("windows_setup.dashboard_snapshot", side_effect=PermissionError("Access denied to native logs")):
            page, payload, _ = self.get()
        self.assertEqual(payload["startup"]["state"], "read_error")
        self.assertIn("Access denied", payload["startup"]["error"])
        self.assertIn("location.reload()", page)

    def test_malformed_csv_and_nonfinite_pnl_show_read_error(self):
        self.folder.mkdir(parents=True)
        ledger = self.folder / "Sim101_R1_events.csv"
        header = 'sequence,timestamp,account,stage,arm,reason,details\n'
        for content in (
            header + '1,"unclosed quotation\n',
            header + '1,2026-10-07T10:01:00-04:00,Sim101,Funded,R1,FILLED,name=MNQ_ENTRY;price=20000;quantity=1;order_id=a\n2,2026-10-07T10:02:00-04:00,Sim101,Funded,R1,EMERGENCY_STOP,net_usd=nan\n',
        ):
            with self.subTest(content=content):
                ledger.write_text(content)
                _, payload, _ = self.get()
                self.assertEqual(payload["startup"]["state"], "read_error")

    def test_server_does_not_serve_arbitrary_files_and_occupied_port_uses_free_port(self):
        with self.assertRaises(HTTPError) as failed:
            urlopen(self.url + 'MNQCalendar.csv', timeout=3)
        self.assertEqual(failed.exception.code, 404)
        extra = windows_setup.create_dashboard_server(self.home, self.run_id, self.server.server_port)
        try:
            self.assertNotEqual(extra.server_port, self.server.server_port)
        finally:
            extra.server_close()

    def test_download_report_preserves_blocker_before_status_rows(self):
        self.folder.mkdir(parents=True)
        ledger = self.folder / "Sim101_P0_events.csv"
        ledger.write_text('sequence,timestamp,account,stage,arm,reason,details\n1,2026-10-07T09:30:00-04:00,Sim101,Funded,P0,ATR_WARMUP,ATR20=0\n')
        with ledger.open('a') as stream:
            for index in range(2, 1002):
                stream.write(f'{index},2026-10-07T11:20:00-04:00,Sim101,Funded,P0,LIVE_STATUS,price=31300;blocked=True\n')
        before = ledger.read_bytes()
        with urlopen(self.url + 'diagnostics.json', timeout=3) as response:
            report = json.loads(response.read())
            self.assertIn('attachment', response.headers['Content-Disposition'])
            self.assertIn('application/json', response.headers['Content-Type'])
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(report['startup']['run_id'], self.run_id)
        self.assertEqual(report['entry_checks'][0]['recorded_checks'][0]['reason'], 'ATR_WARMUP')
        self.assertEqual([row['reason'] for row in report['diagnostic_events']], ['ATR_WARMUP'])
        self.assertEqual(ledger.read_bytes(), before)
        self.assertFalse((self.folder / 'dashboard.html').exists())

    def test_download_report_without_logs_stays_unknown_and_creates_no_files(self):
        with urlopen(self.url + 'diagnostics.json', timeout=3) as response:
            report = json.loads(response.read())
        self.assertEqual(report['startup']['state'], 'waiting_for_logs')
        self.assertEqual(report['entry_checks'], [])
        self.assertEqual(report['total_event_count'], 0)
        self.assertFalse(self.home.exists())

    def test_browser_and_report_show_delayed_delivery_without_modifying_native_logs(self):
        self.folder.mkdir(parents=True)
        received = datetime.now(timezone.utc)
        tick = received - timedelta(minutes=10)
        ledger = self.folder / "Sim101_R1_events.csv"
        ledger.write_text('sequence,timestamp,account,stage,arm,reason,details\n'
                          + f'1,{tick.isoformat()},Sim101,Funded,R1,LIVE_STATUS,price=31300;blocked=True;received_at_et={received.isoformat()};feed_age_seconds=600;max_tick_gap_seconds=90\n')
        before = ledger.read_bytes()
        page, payload, _ = self.get()
        timing = payload["entry_checks"][0]["feed_timing"]
        self.assertEqual(timing["condition"], "DELAYED_OR_CLOCK_OFFSET")
        self.assertEqual(timing["delivery_lag_seconds"], 600)
        self.assertIn("Delivery lag at logged update", page)
        with urlopen(self.url + 'diagnostics.json', timeout=3) as response:
            report = json.loads(response.read())
        self.assertEqual(report["entry_checks"][0]["feed_timing"]["condition"], "DELAYED_OR_CLOCK_OFFSET")
        self.assertEqual(ledger.read_bytes(), before)
        self.assertFalse((self.folder / 'dashboard.html').exists())


if __name__ == "__main__":
    unittest.main()
