from pathlib import Path
import tempfile
import unittest

from bot.backtest import replay
from bot.demo import generate
from bot.models import Config
from bot.report import write_run
from bot.review import diagnose, learning_queue


class LossReviewTests(unittest.TestCase):
    def test_every_loss_has_30_evidence_based_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            ticks, calendar = generate(Path(tmp) / "data", 32)
            engine = replay(ticks, calendar, Config())
            losses = [
                diagnose(t, a, engine.events, engine.config)
                for t, a in zip(engine.trades, engine.trade_audits)
                if t.net_ticks < 0
            ]
            self.assertGreater(len(losses), 0)
            for review in losses:
                self.assertEqual(len(review["diagnostics"]), 30)
                self.assertFalse(review["active_strategy_changed"])
                server = next(
                    c for c in review["diagnostics"] if "broker server" in c["question"]
                )
                self.assertEqual(server["status"], "unknown")
                self.assertIn("no broker server", server["answer"])
            queue = learning_queue(losses, len(engine.trades))
            self.assertEqual(queue["status"], "COLLECT_MORE_SESSIONS")
            self.assertFalse(queue["active_strategy_changed"])
            write_run(engine, Path(tmp) / "run", {})
            self.assertTrue((Path(tmp) / "run" / "loss_reviews.json").exists())

    def test_winning_trades_not_reported_as_losses(self):
        with tempfile.TemporaryDirectory() as tmp:
            ticks, calendar = generate(Path(tmp) / "data", 32)
            engine = replay(ticks, calendar, Config())
            self.assertEqual(len(engine.trades), len(engine.trade_audits))
            for audit in engine.trade_audits:
                self.assertLessEqual(audit["pre_entry_bars"][-1]["end"].hour, 15)
                self.assertIn("max_net_ticks", audit)
                self.assertIn("min_net_ticks", audit)
