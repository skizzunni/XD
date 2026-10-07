import csv
from datetime import date, datetime, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout

from bot.backtest import replay
from bot.data import Calendar, checksum, parse_time
from bot.demo import generate
from bot.models import Config
from main import main


ROOT = Path(__file__).resolve().parents[1]
PRESET = ROOT / "ninjatrader/calendars/mnq-dec26-2026-10-05-06"


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def export(self, source, output):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(["export-nt-calendar", "--calendar", str(source), "--out", str(output)]),
                0,
            )

    def test_warmup_builds_atr_without_trading_before_selected_dates(self):
        ticks, calendar_path = generate(self.root / "fixture", 24)
        raw = json.loads(calendar_path.read_text())
        for row in raw["sessions"][:22]:
            row["trade_enabled"] = False
        calendar_path.write_text(json.dumps(raw))
        result = replay(ticks, calendar_path, Config())
        expected = {row["date"] for row in raw["sessions"][22:]}
        self.assertEqual({t.session for t in result.trades}, expected)
        self.assertEqual(len(result.daily_ranges), 24)
        self.assertTrue(all(r["complete"] for r in result.session_records))
        self.assertTrue(all(r["atr20"] > 0 for r in result.session_records[22:]))
        self.assertIn("WARMUP_ONLY", {v["reason"] for v in result.events})

    def test_existing_calendars_default_to_trading_enabled(self):
        _, path = generate(self.root / "fixture", 22)
        self.assertTrue(all(s.trade_enabled for s in Calendar(path).sessions.values()))

    def test_trade_enabled_requires_boolean(self):
        _, path = generate(self.root / "fixture", 22)
        raw = json.loads(path.read_text())
        for bad in ("false", 0, None):
            raw["sessions"][0]["trade_enabled"] = bad
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError, "trade_enabled must be a boolean"):
                Calendar(path)

    def test_export_retains_news_even_when_another_block_reason_takes_priority(self):
        _, path = generate(self.root / "fixture", 22)
        raw = json.loads(path.read_text())
        row = raw["sessions"][0]
        row["trade_enabled"] = False
        row["roll_day"] = True
        opening = parse_time(row["open"])
        row["news"] = {
            "flags": ["FOMC"],
            "releases": [{"name": "Test release", "timestamp": (opening + timedelta(hours=6)).isoformat(), "revision": None}],
        }
        path.write_text(json.dumps(raw))
        session = Calendar(path).sessions[opening.date()]
        self.assertEqual(session.eligibility, "ROLL_DAY")
        self.assertTrue(session.late_news)
        output = self.root / "calendar.csv"
        self.export(path, output)
        with output.open(newline="") as f:
            next(f)
            row = next(csv.DictReader(f))
        self.assertEqual(row["late_news"], "true")
        self.assertEqual(row["trade_enabled"], "false")

    def test_shipped_calendar_dates_contract_holiday_and_news(self):
        calendar = Calendar(PRESET / "calendar.json")
        self.assertFalse(calendar.synthetic)
        self.assertEqual(len(calendar.sessions), 31)
        self.assertNotIn(date(2026, 9, 7), calendar.sessions)
        self.assertTrue(all(s.contract == "MNQ 12-26" for s in calendar.sessions.values()))
        self.assertEqual(
            {d for d, s in calendar.sessions.items() if s.trade_enabled},
            {date(2026, 10, 5), date(2026, 10, 6)},
        )
        self.assertTrue(calendar.sessions[date(2026, 9, 14)].roll_day)
        for day, flag in ((date(2026, 9, 11), "CPI"), (date(2026, 10, 2), "PAYROLLS"), (date(2026, 9, 30), "GDP"), (date(2026, 9, 16), "FOMC")):
            self.assertIn(flag, calendar.sessions[day].news_flags)
        for day in (date(2026, 9, 30), date(2026, 10, 1)):
            self.assertTrue(calendar.sessions[day].late_news)
        for day in (date(2026, 10, 5), date(2026, 10, 6)):
            self.assertEqual(calendar.sessions[day].eligibility, "ELIGIBLE")
        self.assertEqual(calendar.sessions[date(2026, 10, 6)].open.utcoffset(), timedelta(hours=-4))

    def test_shipped_csv_matches_json_and_can_be_reproduced(self):
        out = self.root / "calendar.csv"
        self.export(PRESET / "calendar.json", out)
        self.assertEqual(out.read_bytes(), (PRESET / "MNQCalendar.csv").read_bytes())
        lines = out.read_text().splitlines()
        self.assertIn(checksum(PRESET / "calendar.json"), lines[0])
        self.assertIn("synthetic=false", lines[0])
        rows = list(csv.DictReader(lines[1:]))
        self.assertEqual(len(rows), 31)
        self.assertEqual({r["date"] for r in rows if r["trade_enabled"] == "true"}, {"2026-10-05", "2026-10-06"})
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.export(PRESET / "calendar.json", out)

    def test_requested_dates_replay_after_contract_specific_synthetic_warmup(self):
        # Synthetic prices exercise scheduling only; they are never market evidence.
        tick_path, generated = generate(self.root / "fixture", 31)
        source = json.loads(generated.read_text())
        target = json.loads((PRESET / "calendar.json").read_text())
        target["synthetic"] = True
        target["source"] = "SYNTHETIC PRICE TEST on the requested calendar dates"
        generated.write_text(json.dumps(target))
        mapping = {
            date.fromisoformat(a["date"]): parse_time(b["open"])
            for a, b in zip(source["sessions"], target["sessions"])
        }
        with tick_path.open(newline="") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames
            rows = list(reader)
        for row in rows:
            original = parse_time(row["timestamp"])
            opening = datetime.combine(original.date(), original.time().replace(hour=9, minute=30), original.tzinfo)
            row["timestamp"] = (mapping[original.date()] + (original - opening)).isoformat()
            row["contract"] = "MNQ 12-26"
        with tick_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        result = replay(tick_path, generated, Config())
        self.assertEqual({t.session for t in result.trades}, {"2026-10-05", "2026-10-06"})
        self.assertEqual(len(result.trades), 2)
        self.assertEqual(len(result.session_records), 31)
        self.assertTrue(all(r["complete"] for r in result.session_records))
        self.assertTrue(all(r["atr20"] > 0 for r in result.session_records[-2:]))


if __name__ == "__main__":
    unittest.main()
