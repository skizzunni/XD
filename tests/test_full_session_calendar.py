import csv
from datetime import date, datetime, time, timedelta
import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from bot.data import Calendar, ET
from bot.fullsession import FullSessionCalendar
from main import main
from scripts.build_full_session_calendar import october_news
import windows_setup


class FullSessionCalendarTests(unittest.TestCase):
    def setUp(self):
        self.path=windows_setup.SIM_PRESET/"calendar.json"
        self.calendar=Calendar(self.path)

    def test_all_enabled_october_dates_have_reviewed_full_hours_and_correct_contract(self):
        extended=FullSessionCalendar(self.calendar)
        enabled=[s for s in extended.sessions.values() if s.trade_enabled]
        self.assertEqual(len(extended.sessions),49)
        self.assertEqual(len(enabled),18)
        self.assertEqual(enabled[0].day,date(2026,10,7))
        self.assertEqual(enabled[-1].day,date(2026,10,30))
        for s in enabled:
            self.assertEqual(s.open,datetime.combine(s.day-timedelta(days=1),time(18),ET))
            self.assertEqual(s.close,datetime.combine(s.day,time(17),ET))
            self.assertEqual(s.contract,"MNQ 12-26")
        self.assertTrue(extended.sessions[date(2026,10,12)].trade_enabled)

    def test_cme_receipts_match_retained_source_hashes_and_native_csv_is_reproducible(self):
        raw=json.loads(self.path.read_text())
        cme=[s for s in raw["sources"] if s["id"].startswith("cme-mnq-")]
        self.assertEqual(len(cme),2)
        snapshots=windows_setup.PROJECT/"ninjatrader/calendars/source-snapshots"
        for source in cme:
            data=gzip.decompress((snapshots/(source["id"]+".gz")).read_bytes())
            self.assertEqual(hashlib.sha256(data).hexdigest(),source["sha256"])
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/"calendar.csv"
            self.assertEqual(main(["export-nt-calendar","--calendar",str(self.path),"--out",str(out)]),0)
            self.assertEqual(out.read_bytes(),(windows_setup.SIM_PRESET/"MNQCalendar.csv").read_bytes())
        rows=list(csv.DictReader(io.StringIO((windows_setup.SIM_PRESET/"MNQCalendar.csv").read_text().split("\n",1)[1])))
        self.assertEqual(len(rows),49)
        self.assertTrue(all(len(r)==13 for r in rows))

    def test_macro_windows_include_cpi_gdp_fomc_and_late_statistical_releases(self):
        calendar=FullSessionCalendar(self.calendar)
        for day,hour,minute in ((14,8,30),(29,8,30),(28,14,0),(28,14,30),(8,16,30)):
            s=calendar.sessions[date(2026,10,day)]
            self.assertTrue(s.news_paused(s.at(hour,minute)),(day,hour,minute))
        parsed=october_news(windows_setup.PROJECT/"ninjatrader/calendars/source-snapshots")
        self.assertTrue(any("GDP" in x["name"] and x["timestamp"].startswith("2026-10-29") for x in parsed))
        self.assertTrue(any("Consumer Price Index" in x["name"] and x["timestamp"].startswith("2026-10-14") for x in parsed))

    def test_historical_holiday_segment_is_excluded_and_future_calendar_is_not_invented(self):
        raw=json.loads(self.path.read_text())
        omitted=raw["historical_globex_segments_excluded"]
        self.assertEqual(omitted[0]["trading_date"],"2026-09-08")
        self.assertNotIn(date(2026,11,2),self.calendar.sessions)
        self.assertTrue(all(not s.trade_enabled for s in self.calendar.sessions.values() if s.day<date(2026,10,7)))


if __name__ == "__main__":
    unittest.main()
