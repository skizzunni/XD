import csv
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from bot.broker import PaperBroker
from bot.dashboard import native_payload
from bot.fullsession import FullSessionEngine
from bot.models import Config,Tick
from tests.test_fullsession import fixture,ticks_for


class QuantityTests(unittest.TestCase):
    def test_limits_and_original_arms(self):
        for q in (0,11,True,2.5):
            with self.assertRaises(ValueError):Config(strategy="R2",paper_contracts=q)
        for arm in ("P0","C1","R1"):
            with self.assertRaises(ValueError):Config(strategy=arm,paper_contracts=2)

    def test_sizes_scale_cash_fees_and_losses_with_identical_per_contract_fills(self):
        stamp=datetime.fromisoformat("2026-10-07T10:00:00-04:00")
        for q in (1,2,5,10):
            for side in (-1,1):
                cfg=Config(strategy="R2",paper_contracts=q,round_turn_fees_usd=1.5)
                broker=PaperBroker(cfg)
                tick=Tick(stamp,20000,1,"MNQ DEC26","a",19999.75,20000.25)
                p=broker.enter(tick,side,20000-side*10)
                price=20000-side*5
                close=replace(tick,price=price,bid=price-.25,ask=price+.25,tick_id="b")
                session=type("Session",(),{"day":stamp.date(),"news_flags":()})()
                t=broker.exit(close,session,"EMERGENCY_STOP")
                self.assertEqual(t.quantity,q)
                self.assertEqual(t.fees_usd,1.5*q)
                self.assertAlmostEqual(t.net_ticks*.5*q,(side*(t.exit_fill-p.entry_fill)*2-1.5)*q)
                self.assertLess(t.net_ticks,0)

    def test_larger_sizes_do_not_silently_raise_the_loss_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            c=fixture(Path(directory)/"calendar.json",1);s=next(iter(c.sessions.values()))
            counts=[]
            for q in (1,2,5,10):
                cfg=Config(strategy="R2",paper_contracts=q,round_turn_fees_usd=1.5,session_loss_budget_usd=100)
                e=FullSessionEngine(cfg,c);e.daily_ranges=[150]*20
                for tick in ticks_for(s):e.process(tick)
                e.finish();counts.append(len(e.trades))
                for trade,audit in zip(e.trades,e.trade_audits):
                    risk=abs(trade.entry_fill-audit["initial_stop"])*2*q+1.5*q+.5*q
                    self.assertLessEqual(risk,100+min(0,audit["day_realized_before"])+1e-8)
                    self.assertEqual(audit["position_quantity"],q)
            self.assertGreater(counts[0],0)
            self.assertEqual(counts[-1],0)

    def test_partial_entries_and_exits_make_one_closed_trade_in_dashboard(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/"R2_events.csv"
            rows=[
                ("PARTIAL","state=PartFilled;error=NoError;name=MNQ_ENTRY"),
                ("FILLED","name=MNQ_ENTRY;order_id=a;trade_id=Sim101:a:new;price=20000;quantity=4;direction=1;exit_profile=TrendRunner"),
                ("FILLED","name=MNQ_ENTRY;order_id=a;trade_id=Sim101:a:new;price=20001;quantity=6;direction=1;exit_profile=TrendRunner"),
                ("EXIT_PART_FILLED","part_net_usd=29.2;quantity=4;remaining_qty=6"),
                ("EXIT_PART_FILLED","part_net_usd=19.8;quantity=6;remaining_qty=0"),
                ("TRADE_CLOSED","trade_id=Sim101:a:new;quantity=10;net_usd=49;fees=15;exit_reason=TARGET_EXIT"),
            ]
            with p.open("w",newline="") as f:
                w=csv.writer(f);w.writerow(["sequence","timestamp","account","stage","arm","reason","details"])
                for i,(reason,detail) in enumerate(rows):w.writerow([i+1,f"2026-10-07T10:00:0{i}-04:00","Sim101","Funded","R2",reason,detail])
            payload=native_payload([p]);self.assertEqual(len(payload["trades"]),1)
            t=payload["trades"][0]
            self.assertEqual(t["quantity"],10)
            self.assertAlmostEqual(t["entry_fill"],20000.6)
            self.assertEqual(t["net_usd"],49)
            self.assertEqual(t["exit_reason"],"TARGET_EXIT")
            self.assertEqual(payload["pending_positions"],[])
            self.assertEqual(payload["entry_checks"][0]["recorded_checks"],[])
