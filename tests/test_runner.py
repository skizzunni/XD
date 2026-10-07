from contextlib import redirect_stdout
import csv
from dataclasses import replace
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import random
import tempfile
import unittest

from bot.dashboard import run_payload
from bot.fullsession import FullSessionEngine
from bot.models import Config
from bot.review import diagnose
from bot.strategy import runner_stop
from main import main
from tests.test_fullsession import fixture, ticks_for


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"calendar.json"
        self.calendar=fixture(self.path,1)
        self.session=next(iter(self.calendar.sessions.values()))

    def tearDown(self):
        self.tmp.cleanup()

    def engine(self,config):
        e=FullSessionEngine(config,self.calendar)
        e.daily_ranges=[150]*20
        return e

    def feed_sustained_trend(self,e):
        # Synthetic branch fixture: sustain each entered trend to exercise the
        # holding deadline and frozen profile, not estimate its profitability.
        for t in ticks_for(self.session):
            p=e.broker.position
            if p:
                minutes=int((t.timestamp-datetime.fromisoformat(p.entry_time)).total_seconds()/60)
                price=p.entry_fill+p.direction*minutes*.25
                t=replace(t,price=price,bid=price-.25,ask=price+.25)
            e.process(t)
            yield t

    def test_runner_stop_is_causal_tick_aligned_and_never_loosened(self):
        rng=random.Random(70726)
        for d in (-1,1):
            fill,risk,peak,stop=20000,10,20000,20000-d*10
            for _ in range(1000):
                quote=20000+d*rng.randrange(-80,400)*.25
                peak=max(peak,quote) if d>0 else min(peak,quote)
                proposed=runner_stop(d,fill,risk,peak,stop,quote)
                self.assertGreaterEqual(d*(proposed-stop),0)
                self.assertAlmostEqual(proposed/.25,round(proposed/.25))
                if proposed!=stop:self.assertGreater(d*(quote-proposed),.25)
                stop=proposed
        self.assertEqual(runner_stop(1,20000,10,20014.75,19990,20014.75),19990)

    def test_runner_holds_trends_longer_and_respects_the_reviewed_close(self):
        e=self.engine(Config(strategy="R2",exit_profile="TrendRunner",adaptive_quality=False))
        for _ in self.feed_sustained_trend(e):pass
        e.finish()
        durations=[(datetime.fromisoformat(t.exit_time)-datetime.fromisoformat(t.entry_time)).total_seconds()/60 for t in e.trades]
        self.assertTrue(any(n>30 for n in durations))
        self.assertTrue(any(n>=75 for n in durations))
        self.assertTrue(any(v["reason"]=="TRAILING_STOP" for v in e.events))
        self.assertTrue(all(datetime.fromisoformat(t.exit_time)<=self.session.close-timedelta(minutes=5) for t in e.trades))
        self.assertIsNone(e.broker.position)

    def test_trailing_gap_records_an_actual_loss_and_thirty_diagnostics(self):
        e=self.engine(Config(strategy="R2",exit_profile="TrendRunner"))
        for t in ticks_for(self.session):
            e.process(t)
            if e.broker.position:break
        p=e.broker.position
        self.assertIsNone(p.target)
        old=p.stop;peak=p.entry_fill+2*p.initial_risk
        e.process(replace(t,timestamp=t.timestamp+timedelta(minutes=1),price=peak,bid=peak-.25,ask=peak+.25,tick_id="peak"))
        self.assertGreater(p.stop,old)
        gap=p.stop-30
        e.process(replace(t,timestamp=t.timestamp+timedelta(minutes=2),price=gap,bid=gap-.25,ask=gap+.25,tick_id="gap"))
        self.assertEqual(e.trades[-1].exit_reason,"EMERGENCY_STOP")
        self.assertLess(e.trades[-1].net_ticks,0)
        self.assertEqual(len(diagnose(e.trades[-1],e.trade_audits[-1],e.events,e.config)["diagnostics"]),30)

    def test_runner_preserves_daytime_profile_across_1600(self):
        e=self.engine(Config(strategy="R2",exit_profile="TrendRunner",adaptive_quality=False))
        crossed=False
        for t in self.feed_sustained_trend(e):
            if t.timestamp>=self.session.at(16,0) and e.broker.position:
                if datetime.fromisoformat(e.broker.position.entry_time)<self.session.at(16,0):
                    self.assertEqual(e.entry_rules["regime"],"RTH")
                    self.assertEqual(e.entry_rules["hold_minutes"],90)
                    crossed=True
        self.assertTrue(crossed)

    def test_fixed_runner_comparison_freezes_costs_quotes_and_adaptation(self):
        c=fixture(self.path,24);ticks=Path(self.tmp.name)/"ticks.csv"
        with ticks.open("w",newline="") as f:
            w=csv.writer(f);w.writerow(["timestamp","price","volume","contract","tick_id","bid","ask"])
            for s in c.sessions.values():
                for t in ticks_for(s):w.writerow([t.timestamp.isoformat(),t.price,t.volume,t.contract,t.tick_id,t.bid,t.ask])
        out=Path(self.tmp.name)/"comparison"
        config=Path(__file__).resolve().parents[1]/"config.full-session-runner-paper.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--config",str(config),"compare-r2-exits","--ticks",str(ticks),"--calendar",str(self.path),"--out",str(out)]),0)
        comparison=json.loads((out/"comparison.json").read_text())
        self.assertFalse(comparison["automatic_promotion"])
        manifests=[json.loads((out/p/"manifest.json").read_text()) for p in ("Fixed","TrendRunner")]
        self.assertEqual(manifests[0]["tick_checksum"],manifests[1]["tick_checksum"])
        for m in manifests:
            self.assertFalse(m["config"]["adaptive_quality"])
            self.assertEqual(m["config"]["round_turn_fees_usd"],1.5)
        self.assertTrue(all(v["decision"]=="SYNTHETIC_SMOKE_TEST_ONLY" for v in comparison["profiles"].values()))
        self.assertEqual({t["exit_profile"] for t in run_payload(out/"TrendRunner")["trades"]},{"TrendRunner"})
        sizes=Path(self.tmp.name)/"sizes"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--config",str(config),"compare-r2-sizing","--ticks",str(ticks),"--calendar",str(self.path),"--out",str(sizes)]),0)
        sweep=json.loads((sizes/"comparison.json").read_text())
        self.assertEqual(sweep["quantities"],[1,2,5,10])
        self.assertEqual(sweep["session_loss_budget_usd"],100)
        self.assertEqual(len(sweep["profiles"]),8)
        self.assertTrue(all(r["paper_contracts"] in (1,2,5,10) for r in sweep["profiles"].values()))

    def test_runner_is_rejected_for_original_arms(self):
        for arm in ("P0","C1","R1"):
            with self.assertRaisesRegex(ValueError,"separate R2"):
                Config(strategy=arm,exit_profile="TrendRunner")

