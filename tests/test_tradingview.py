import hashlib
import json
from pathlib import Path
import re
import unittest

from scripts.build_tradingview import CALENDAR, OUTPUT, questions, render
from bot.data import Calendar
from bot.fullsession import FullSessionCalendar

ROOT = Path(__file__).resolve().parents[1]


class TradingViewReleaseTests(unittest.TestCase):
    def test_release_is_exact_deterministic_render(self):
        self.assertEqual(OUTPUT.read_text(), render())
        self.assertEqual(render(), render())
        self.assertNotIn("// @GENERATED_CALENDAR@", OUTPUT.read_text())

    def test_calendar_provenance_and_all_session_fields(self):
        source = OUTPUT.read_text()
        self.assertIn(hashlib.sha256(CALENDAR.read_bytes()).hexdigest(), source)
        rows = json.loads(CALENDAR.read_text())["sessions"]
        raw = re.search(r"var array<int> calDay = array.from\(([^\n]+)\)", source).group(1)
        self.assertEqual(len(raw.split(",")), len(rows))
        for row in rows:
            self.assertEqual(row["contract"], "MNQ 12-26")
            for timestamp in (row["open"], row["close"], row["globex"]["open"], row["globex"]["close"]):
                from scripts.build_tradingview import milliseconds
                self.assertIn(str(milliseconds(timestamp)), source)

    def test_enabled_dates_match_fullsession_eligibility(self):
        calendar = FullSessionCalendar(Calendar(CALENDAR))
        expected = [s.eligibility == "ELIGIBLE" for s in calendar.sessions.values()]
        raw = re.search(r"var array<bool> enabled = array.from\(([^\n]+)\)", OUTPUT.read_text()).group(1)
        actual = [v.strip() == "true" for v in raw.split(",")]
        self.assertEqual(actual, expected)
        self.assertEqual(sum(actual), 18)

    def test_every_news_window_is_clamped_to_actual_session(self):
        from scripts.build_tradingview import milliseconds
        source = OUTPUT.read_text()
        starts = [int(v) for v in re.search(r"var array<int> newsStart = array.from\(([^\n]+)\)", source).group(1).split(",")]
        ends = [int(v) for v in re.search(r"var array<int> newsEnd = array.from\(([^\n]+)\)", source).group(1).split(",")]
        expected = []
        for row in json.loads(CALENDAR.read_text())["sessions"]:
            a, b = milliseconds(row["globex"]["open"]), milliseconds(row["globex"]["close"])
            for event in row["news"]["releases"]:
                ts = milliseconds(event["timestamp"])
                start, end = max(a, ts - 300000), min(b, ts + 600000)
                if start < end:
                    expected.append((start, end))
        self.assertEqual(list(zip(starts, ends)), expected)

    def test_loss_questions_are_the_engine_questions(self):
        values = questions()
        self.assertEqual(len(values), 30)
        self.assertEqual(len(set(values)), 30)
        for question in values:
            self.assertIn(json.dumps(question), OUTPUT.read_text())
        self.assertIn("Was the stop resting on a broker server?", values)
        self.assertIn("Would a proposed fix generalize to untouched future sessions?", values)

    def test_release_has_no_credentials_and_clipboard_path_is_local(self):
        source = OUTPUT.read_text()
        self.assertNotIn("databento", source.lower())
        self.assertNotIn("API_KEY", source)
        launcher = (ROOT / "COPY-TRADINGVIEW.cmd").read_text()
        self.assertIn("%~dp0tradingview\\MNQ_R2_Paper.pine", launcher)
        self.assertIn("-LiteralPath", launcher)
        self.assertIn("Set-Clipboard -Value $source", launcher)
        self.assertNotIn("ExecutionPolicy", launcher)

    def test_guide_names_simulation_and_native_acceptance(self):
        guide = (ROOT / "tradingview/START-HERE.md").read_text()
        for text in ("Strategy Tester", "delayed", "cash per contract", "per-side", "Pine Editor", "not the official Pine compiler", "1% margin", "one five-minute bar"):
            self.assertIn(text, guide)


if __name__ == "__main__":
    unittest.main()
