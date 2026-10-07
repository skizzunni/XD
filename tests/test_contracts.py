import csv
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from bot.backtest import replay
from bot.contracts import contract_key, same_contract
from bot.data import Calendar, read_ticks
from bot.demo import generate, generate_rolling
from bot.engine import Engine
from bot.index import TickIndex
from bot.models import Config, Tick


class ContractTests(unittest.TestCase):
    def test_quarterly_aliases_match_only_the_same_expiry(self):
        for label, month in (("MAR", "03"), ("JUN", "06"), ("SEP", "09"), ("DEC", "12")):
            for year in range(100):
                canonical = f"MNQ {month}-{year:02}"
                self.assertEqual(contract_key(f"MNQ {label}{year:02}"), canonical)
                self.assertTrue(same_contract(f"mnq {label.lower()}{year:02}", canonical))
                self.assertFalse(same_contract(canonical, f"MNQ {month}-{(year+1)%100:02}"))
        for other in ("MNQ SEP26", "MNQ DEC27", "NQ DEC26", "MNQ", "MNQ ##-##", "MNQ DEC2026", "MNQ 12-2026", "MNQ DEC26 extra"):
            self.assertFalse(same_contract("MNQ 12-26", other))
        self.assertEqual(contract_key("MNQ-TEST"), "MNQ-TEST")
        self.assertEqual(contract_key("MNQ JAN26"), "MNQ JAN26")

    def test_alias_history_warms_atr_and_preserves_baseline_trades(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "fixture"
            ticks, calendar_path = generate(root, 24)
            baseline = replay(ticks, calendar_path, Config())
            calendar = json.loads(calendar_path.read_text())
            for row in calendar["sessions"]:
                row["contract"] = "MNQ 12-26"
            calendar_path.write_text(json.dumps(calendar))
            with ticks.open(newline="") as stream:
                reader = csv.DictReader(stream)
                fields, rows = reader.fieldnames, list(reader)
            for row in rows:
                row["contract"] = "MNQ DEC26"
            with ticks.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            aliased = replay(ticks, calendar_path, Config())
            self.assertGreater(len(aliased.trades), 0)
            self.assertEqual([t.net_ticks for t in aliased.trades], [t.net_ticks for t in baseline.trades])
            self.assertEqual(aliased.daily_ranges, baseline.daily_ranges)
            self.assertTrue(all(t.contract == "MNQ DEC26" for t in aliased.trades))
            self.assertFalse(any(e["reason"] == "UNRESOLVED_CONTRACT" for e in aliased.events))

    def test_alias_cannot_bypass_tick_deduplication_in_memory_or_on_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            _, path = generate(Path(directory) / "fixture", 24)
            raw = json.loads(path.read_text())
            for row in raw["sessions"]:
                row["contract"] = "MNQ 12-26"
            path.write_text(json.dumps(raw))
            calendar = Calendar(path)
            session = next(iter(calendar.sessions.values()))
            tick = Tick(session.open, 20000, 1, "MNQ DEC26", "same-id", None, None)
            for disk in (False, True):
                index = TickIndex() if disk else None
                try:
                    engine = Engine(Config(), calendar, tick_index=index)
                    engine.process(tick)
                    with self.assertRaisesRegex(ValueError, "DUPLICATE_TICK"):
                        engine.process(replace(tick, contract="MNQ 12-26"))
                finally:
                    if index:
                        index.close()

    def test_r1_aliases_preserve_reentry_loss_audits_and_learning(self):
        with tempfile.TemporaryDirectory() as directory:
            ticks, path = generate_rolling(Path(directory) / "fixture", 24, losses=True)
            baseline = replay(ticks, path, Config(strategy="R1"))
            raw = json.loads(path.read_text())
            for row in raw["sessions"]:
                row["contract"] = "MNQ 12-26"
            path.write_text(json.dumps(raw))
            engine = Engine(Config(strategy="R1"), Calendar(path))
            for index, tick in enumerate(read_ticks(ticks)):
                engine.process(replace(tick, contract="MNQ DEC26" if index % 2 else "MNQ 12-26"))
            engine.finish()
            self.assertGreater(len(engine.trades), 5)
            self.assertTrue(any(t.net_ticks < 0 for t in engine.trades))
            self.assertEqual([t.net_ticks for t in engine.trades], [t.net_ticks for t in baseline.trades])
            self.assertEqual(engine.trade_audits, baseline.trade_audits)
            self.assertEqual(engine.learner.samples, baseline.learner.samples)


if __name__ == "__main__":
    unittest.main()
