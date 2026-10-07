from contextlib import redirect_stdout
import csv
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest

from bot.data import Calendar, ET
from bot.fullsession import FullSessionCalendar, FullSessionEngine, futures_day
from bot.models import Config, Tick
from bot.report import engine_report
from bot.review import diagnose
from bot.dashboard import run_payload
from main import main


def fixture(path, count=24, start=date(2026, 1, 5), news=None):
    rows, day = [], start
    for _ in range(count):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        rows.append({"date": str(day), "open": datetime.combine(day, time(9,30), ET).isoformat(),
                     "close": datetime.combine(day, time(16), ET).isoformat(), "contract": "MNQ 12-26",
                     "roll_day": False, "trade_enabled": True,
                     "news": {"flags": [], "releases": news or []},
                     "globex": {"open": datetime.combine(day-timedelta(days=1), time(18), ET).isoformat(),
                                "close": datetime.combine(day, time(17), ET).isoformat(), "breaks": []}})
        day += timedelta(days=1)
    path.write_text(json.dumps({"version": 1, "source": "SYNTHETIC FULL SESSION TEST", "synthetic": True,
                                "news_as_of": "2026-01-01T00:00:00-05:00", "sessions": rows}))
    return FullSessionCalendar(Calendar(path))


def ticks_for(session, losses=False):
    bars = [(0,3,-1,2),(2,5,1,4),(4,7,3,6),(6,9,5,8),(8,9,6.5,7),(7,13,6.5,12)]
    for minute in range(int((session.close-session.open).total_seconds()//60)):
        bar, inside = divmod(minute, 5)
        cycle, index = divmod(bar, 6)
        o, high, low, close = bars[index]
        if losses and index == 0 and cycle:
            high, low = 3, -10
        price = 20000 + cycle*12 + (o, high, low, close, close)[inside]
        yield Tick(session.open+timedelta(minutes=minute), price, 1, "MNQ DEC26",
                   f"{session.day}-{minute}", price-0.25, price+0.25)


class FullSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/"calendar.json"
        self.calendar = fixture(self.path, 1)
        self.session = next(iter(self.calendar.sessions.values()))

    def tearDown(self):
        self.tmp.cleanup()

    def engine(self, config=None):
        e = FullSessionEngine(config or Config(strategy="R2", break_even_trigger_r=1), self.calendar)
        e.daily_ranges = [150]*20
        return e

    def test_sunday_start_midnight_and_monday_map_to_one_risk_session(self):
        self.assertEqual(self.session.open.weekday(), 6)
        for hour in (18,23):
            self.assertEqual(futures_day(self.session.open.replace(hour=hour)), self.session.day)
        self.assertEqual(futures_day(self.session.at(0,0)), self.session.day)
        e = self.engine(Config(strategy="R2", rolling_momentum_threshold=1))
        e.process(next(ticks_for(self.session)))
        e.day_realized = -25
        for tick in list(ticks_for(self.session))[1:]:
            e.process(tick)
            if tick.timestamp >= self.session.at(0,0):
                break
        self.assertEqual(e.day_realized, -25)
        self.assertEqual(e.session.day, self.session.day)
        self.assertFalse(e.quality_fault)

    def test_full_day_scans_overnight_and_cash_without_trade_count_cap(self):
        e = self.engine()
        for tick in ticks_for(self.session):
            e.process(tick)
        e.finish()
        self.assertGreater(len(e.trades), 5)
        entry_times = [datetime.fromisoformat(t.entry_time) for t in e.trades]
        self.assertTrue(any(t.date() < self.session.day for t in entry_times))
        self.assertTrue(any(time(0) <= t.time() < time(9,30) for t in entry_times))
        self.assertTrue(any(time(9,30) <= t.time() < time(16) for t in entry_times))
        self.assertTrue(all(self.session.open+timedelta(minutes=30) <= t <= self.session.close-timedelta(minutes=15) for t in entry_times))
        self.assertTrue(all(datetime.fromisoformat(t.exit_time) < self.session.close for t in e.trades))
        self.assertIsNone(e.broker.position)
        self.assertTrue(e.session_records[0]["complete"])

    def test_overnight_rules_and_learning_are_separate_from_cash(self):
        e = self.engine()
        e.process(next(ticks_for(self.session)))
        night, cash = e.rolling_rules(self.session.at(2,0)), e.rolling_rules(self.session.at(11,0))
        self.assertEqual((night["efficiency"],night["stop_atr"],night["target_r"],night["hold_minutes"]), (.55,.10,1.25,20))
        self.assertEqual((cash["stop_atr"],cash["target_r"],cash["hold_minutes"]), (.20,1.5,30))
        for n in range(8):
            e.overnight_learner.record(str(n), self.session.open+timedelta(minutes=n), 1, -1)
        self.assertAlmostEqual(e.quality_learner(self.session.at(2,0)).state(1,self.session.at(2,0))["min_efficiency"], .70)
        self.assertEqual(e.quality_learner(self.session.at(11,0)).state(1,self.session.at(11,0))["observations"], 0)

    def test_quote_and_spread_filter_prevent_entries_without_stopping_scanning(self):
        for missing in (True,False):
            e = self.engine()
            for tick in ticks_for(self.session):
                e.process(replace(tick,bid=None,ask=None) if missing else replace(tick,bid=tick.price-1,ask=tick.price+1))
            self.assertEqual(e.trades, [])
            self.assertFalse(e.blocked)
            self.assertTrue(any(x.get("detail")=="R2_QUOTE_OR_SPREAD_FILTER" for x in e.events))

    def test_loss_budget_survives_midnight_and_every_loss_has_a_trade_audit(self):
        e = self.engine(Config(strategy="R2", session_loss_budget_usd=20))
        for tick in ticks_for(self.session, losses=True):
            e.process(tick)
        e.finish()
        self.assertTrue(e.blocked)
        self.assertTrue(all(t.net_ticks < 0 for t in e.trades))
        self.assertEqual(len(e.trade_audits),len(e.trades))
        self.assertLessEqual(e.day_realized,0)
        self.assertLessEqual(len(e.trades),3)
        self.assertLessEqual(e.day_realized,-20)
        for trade, audit in zip(e.trades,e.trade_audits):
            self.assertEqual(len(diagnose(trade,audit,e.events,e.config)["diagnostics"]),30)

    def test_expected_maintenance_and_weekend_prints_cannot_open_trades(self):
        e = self.engine()
        for timestamp in (self.session.open-timedelta(minutes=30), self.session.at(17,30),
                          datetime(2026,1,10,12,tzinfo=ET)):
            e.process(Tick(timestamp,20000,1,"MNQ DEC26",timestamp.isoformat(),19999.75,20000.25))
        self.assertEqual(e.events, [])
        self.assertIsNone(e.broker.position)

    def test_pre_cash_news_pause_is_included_and_cannot_open_an_entry(self):
        release = {"name":"Synthetic 08:30 release","timestamp":self.session.at(8,30).isoformat(),"revision":None}
        self.calendar = fixture(self.path,1,news=[release])
        s = next(iter(self.calendar.sessions.values()))
        self.assertTrue(s.news_paused(s.at(8,25)))
        self.assertFalse(s.news_paused(s.at(8,40)))
        e = self.engine()
        for tick in ticks_for(s):
            e.process(tick)
        self.assertFalse(any(s.at(8,25)<=datetime.fromisoformat(t.entry_time)<s.at(8,40) for t in e.trades))

    def test_missing_reviewed_full_hours_and_missing_cash_history_are_not_fabricated(self):
        raw=json.loads(self.path.read_text());del raw["sessions"][0]["globex"]
        self.path.write_text(json.dumps(raw))
        with self.assertRaisesRegex(ValueError,"reviewed Globex"):
            FullSessionCalendar(Calendar(self.path))
        e = self.engine()
        for tick in ticks_for(self.session):
            if not self.session.at(10,0)<=tick.timestamp<self.session.at(10,5):
                e.process(tick)
        e.finish()
        self.assertFalse(e.session_records[0]["complete"])
        self.assertEqual(e.daily_ranges,[])

    def test_atr_warmup_and_deterministic_full_session_replay(self):
        calendar=fixture(self.path,24)
        outputs=[]
        for _ in range(2):
            e=FullSessionEngine(Config(strategy="R2"),calendar)
            for session in calendar.sessions.values():
                for tick in ticks_for(session):
                    e.process(tick)
            e.finish()
            self.assertGreater(len(e.trades),100)
            self.assertTrue(all(r["complete"] for r in e.session_records))
            self.assertEqual(len(e.learner.samples)+len(e.overnight_learner.samples),len(e.trades))
            report=engine_report(e,True)
            self.assertIsNone(report["trade_count_cap"])
            self.assertEqual(report["learning_samples"],len(e.trades))
            outputs.append((e.trades,e.events,e.trade_audits))
        self.assertEqual(outputs[0],outputs[1])

    def test_evening_news_flattens_an_open_trade_then_resumes_fresh_setups(self):
        release={"name":"Synthetic evening release","timestamp":(self.session.open+timedelta(minutes=40)).isoformat(),"revision":None}
        self.calendar=fixture(self.path,1,news=[release])
        s=next(iter(self.calendar.sessions.values()));e=self.engine()
        for tick in ticks_for(s):
            e.process(tick)
        e.finish()
        exits=[t for t in e.trades if t.exit_reason=="NEWS_EXIT"]
        self.assertEqual(len(exits),1)
        self.assertEqual(datetime.fromisoformat(exits[0].exit_time),s.open+timedelta(minutes=35))
        self.assertTrue(any(datetime.fromisoformat(t.entry_time)>=s.open+timedelta(minutes=50) for t in e.trades))
        self.assertFalse(e.quality_fault)

    def test_reviewed_break_flattens_before_closure_and_preserves_the_risk_day(self):
        raw=json.loads(self.path.read_text())
        start=self.session.open+timedelta(minutes=35);end=start+timedelta(minutes=15)
        raw["sessions"][0]["globex"]["breaks"]=[[start.isoformat(),end.isoformat()]]
        self.path.write_text(json.dumps(raw));self.calendar=FullSessionCalendar(Calendar(self.path))
        s=next(iter(self.calendar.sessions.values()));e=self.engine()
        for tick in ticks_for(s):
            if not start<=tick.timestamp<end:
                e.process(tick)
        e.finish()
        exits=[t for t in e.trades if t.exit_reason=="MARKET_BREAK_EXIT"]
        self.assertEqual(len(exits),1)
        self.assertEqual(datetime.fromisoformat(exits[0].exit_time),start-timedelta(minutes=1))
        self.assertFalse(e.quality_fault)
        self.assertTrue(any(datetime.fromisoformat(t.entry_time)>end for t in e.trades))
        self.assertEqual(e.session.day,s.day)

    def test_missing_flatten_path_is_reported_instead_of_fabricating_a_fill(self):
        e=self.engine()
        for tick in ticks_for(self.session):
            e.process(tick)
            if tick.timestamp>=self.session.open+timedelta(minutes=30):
                break
        self.assertIsNotNone(e.broker.position)
        closed=Tick(self.session.close,20000,1,"MNQ DEC26","closed",19999.75,20000.25)
        with self.assertRaisesRegex(ValueError,"UNRESOLVED_POSITION"):
            e.process(closed)
        self.assertIsNotNone(e.broker.position)

    def test_daylight_saving_weekends_preserve_the_eastern_futures_date(self):
        for start,utc_hour in ((date(2026,3,9),22),(date(2026,11,2),23)):
            c=fixture(self.path,1,start=start);s=next(iter(c.sessions.values()))
            self.assertEqual(s.open.astimezone(timezone.utc).hour,utc_hour)
            self.assertEqual(futures_day(s.open),start)
            self.assertEqual(futures_day(s.at(0,0)),start)

    def test_cash_only_warmup_does_not_require_thin_old_overnight_ticks(self):
        raw=json.loads(self.path.read_text());raw["sessions"][0]["trade_enabled"]=False
        self.path.write_text(json.dumps(raw));self.calendar=FullSessionCalendar(Calendar(self.path))
        s=next(iter(self.calendar.sessions.values()));e=FullSessionEngine(Config(strategy="R2"),self.calendar)
        for t in ticks_for(s):
            if s.cash_open<=t.timestamp<s.cash_close:e.process(t)
        e.finish()
        self.assertTrue(e.session_records[0]["complete"])
        self.assertEqual(len(e.daily_ranges),1)
        self.assertEqual(e.trades,[])
        self.assertFalse(e.quality_fault)

    def test_shortened_reviewed_session_is_excluded_from_entries(self):
        raw=json.loads(self.path.read_text())
        raw["sessions"][0]["close"]=self.session.at(13,0).isoformat()
        raw["sessions"][0]["globex"]["close"]=self.session.at(13,0).isoformat()
        self.path.write_text(json.dumps(raw));self.calendar=FullSessionCalendar(Calendar(self.path))
        s=next(iter(self.calendar.sessions.values()));e=self.engine()
        for tick in ticks_for(s):e.process(tick)
        e.finish()
        self.assertEqual(e.trades,[])
        self.assertEqual(e.session_records[0]["eligibility"],"EARLY_CLOSE")

    def test_r2_cli_replay_writes_loss_reviews_and_separate_dashboard_learning(self):
        calendar=fixture(self.path,24)
        ticks_path=Path(self.tmp.name)/"ticks.csv"
        with ticks_path.open("w",newline="") as stream:
            writer=csv.writer(stream)
            writer.writerow(["timestamp","price","volume","contract","tick_id","bid","ask"])
            for s in calendar.sessions.values():
                for t in ticks_for(s,losses=s.day==max(calendar.sessions)):
                    writer.writerow([t.timestamp.isoformat(),t.price,t.volume,t.contract,t.tick_id,t.bid,t.ask])
        out=Path(self.tmp.name)/"run"
        config=Path(__file__).resolve().parents[1]/"config.full-session-paper.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--config",str(config),"replay","--strategy","R2","--ticks",str(ticks_path),"--calendar",str(self.path),"--out",str(out)]),0)
        report=json.loads((out/"report.json").read_text())
        self.assertEqual(report["strategy"],"R2")
        self.assertIsNone(report["trade_count_cap"])
        reviews=json.loads((out/"loss_reviews.json").read_text())
        self.assertGreater(len(reviews),0)
        self.assertTrue(all(len(r["diagnostics"])==30 for r in reviews))
        payload=run_payload(out)
        self.assertEqual({s["regime"] for s in payload["learning_status"]},{"RTH","OVERNIGHT"})
        self.assertTrue(payload["synthetic"])


if __name__ == "__main__":
    unittest.main()
