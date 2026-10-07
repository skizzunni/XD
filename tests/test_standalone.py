from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from bot.databento_feed import HistoryLoader, MinuteBar, TradeDecoder, atr_seed, raw_symbol, validate_cash_bars
from bot.data import ET
from bot.models import Config, Tick
from bot.standalone import PaperRuntime
from standalone_paper import BufferedCapture, DatabentoWorker, create_server
from tests.test_fullsession import fixture, ticks_for


class StandaloneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.calendar = self.root / "calendar.json"
        self.full = fixture(self.calendar, 24)
        self.session = list(self.full.sessions.values())[-1]
        self.config = Config(strategy="R2", adaptive_sizing=True, paper_contracts=2,
                             exit_profile="TrendRunner", break_even_trigger_r=1)

    def tearDown(self):
        self.tmp.cleanup()

    def runtime(self, directory="account", synthetic=True, config=None):
        runtime = PaperRuntime(config or self.config, self.calendar, self.root / directory, synthetic=synthetic)
        self.addCleanup(runtime.close)
        return runtime

    def seed(self, runtime):
        runtime.seed({"target_day": str(self.session.day), "ranges": [150] * 20,
                      "previous_close": 20000, "previous_contract": "MNQ 12-26",
                      "cash_days": [str(day) for day in self.full.sessions if day < self.session.day][-21:],
                      "source": "SYNTHETIC TEST"})

    def real_runtime(self):
        raw = json.loads(self.calendar.read_text())
        raw.update(synthetic=False, source="VERIFIED LOCAL TEST")
        self.calendar.write_text(json.dumps(raw))
        runtime = self.runtime(synthetic=False)
        self.seed(runtime)
        return runtime

    def test_real_mode_rejects_synthetic_calendar_and_wrong_config(self):
        with self.assertRaisesRegex(ValueError, "real calendar"):
            self.runtime(synthetic=False)
        with self.assertRaisesRegex(ValueError, "requires R2"):
            self.runtime(config=Config())

    def test_capture_trade_reviews_and_restart_reconstruct_exact_results(self):
        runtime = self.runtime()
        self.seed(runtime)
        for tick in ticks_for(self.session, losses=True):
            runtime.ingest(tick, tick.timestamp)
        before = runtime.snapshot(self.session.close - timedelta(seconds=1))
        self.assertGreater(before["runtime"]["received_ticks"], 1000)
        self.assertGreater(len(before["trades"]), 0)
        self.assertGreater(len(before["loss_reviews"]), 0)
        self.assertTrue(all(len(review["diagnostics"]) == 30 for review in before["loss_reviews"]))
        self.assertEqual(len(runtime.engine.sizer.samples), len(runtime.engine.trades))
        self.assertEqual(len(runtime.engine.seen_ids), 0)
        runtime.close()
        restored = self.runtime()
        after = restored.snapshot(self.session.close - timedelta(seconds=1))
        for key in ("trades", "loss_reviews", "sizing_status", "learning_status"):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(before["runtime"]["received_ticks"], after["runtime"]["received_ticks"])
        self.assertAlmostEqual(restored.engine.day_realized, sum(t["net_usd"] for t in after["trades"]))
        restored.close()

    def test_single_owner_config_guard_and_journal_tamper_detection(self):
        runtime = self.runtime()
        with self.assertRaises((BlockingIOError, OSError)):
            self.runtime()
        self.seed(runtime)
        runtime.close()
        with self.assertRaisesRegex(ValueError, "different strategy"):
            self.runtime(config=replace(self.config, session_loss_budget_usd=200))
        with sqlite3.connect(self.root / "account/paper.sqlite3") as connection:
            connection.execute("UPDATE operations SET body='{}' WHERE sequence=1")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.runtime()

    def test_duplicate_trade_is_rejected_without_counting_twice(self):
        runtime = self.runtime()
        self.seed(runtime)
        tick = next(ticks_for(self.session))
        runtime.ingest(tick, tick.timestamp)
        runtime.ingest(tick, tick.timestamp)
        self.assertEqual(runtime.received, 1)
        self.assertTrue(runtime.fatal)
        self.assertIn("DUPLICATE_TICK", runtime.error)
        runtime.close()

    def test_cold_attachment_drops_fragment_without_inventing_history_or_trades(self):
        runtime = self.runtime()
        first = list(ticks_for(self.session))[17]
        runtime.ingest(first, first.timestamp)
        self.assertEqual(runtime.received, 1)
        self.assertEqual(runtime.engine.trades, [])
        self.assertIsNone(runtime.engine.atr)
        self.assertFalse(runtime.engine.quality_fault)
        self.seed(runtime)
        for tick in list(ticks_for(self.session))[18:60]:
            runtime.ingest(tick, tick.timestamp)
        self.assertTrue(all(b.start >= first.timestamp for b in runtime.engine.bars))
        runtime.close()

    def test_http_snapshot_updates_from_capture_and_exposes_no_arbitrary_files(self):
        runtime = self.runtime()
        self.seed(runtime)
        server = create_server(runtime, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/"
        try:
            with urlopen(url + "diagnostics.json") as response:
                first = json.load(response)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            tick = next(ticks_for(self.session))
            runtime.ingest(tick, tick.timestamp)
            with urlopen(url + "diagnostics.json") as response:
                second = json.load(response)
            self.assertEqual(first["runtime"]["received_ticks"], 0)
            self.assertEqual(second["runtime"]["received_ticks"], 1)
            self.assertEqual(second["live_status"][0]["timestamp"], tick.timestamp.isoformat())
            with urlopen(url) as response:
                self.assertIn("Feed and recorder", response.read().decode())
            with self.assertRaises(HTTPError):
                urlopen(url + "paper.sqlite3")
            self.assertFalse(list(self.root.rglob("*.html")))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            runtime.close()

    def test_delay_blocks_paper_fills_but_still_captures_records(self):
        # Real clock rules with a real-marked test calendar; no network calls.
        raw = json.loads(self.calendar.read_text())
        raw["synthetic"] = False
        raw["source"] = "VERIFIED LOCAL TEST"
        self.calendar.write_text(json.dumps(raw))
        runtime = self.runtime(synthetic=False)
        self.seed(runtime)
        for tick in list(ticks_for(self.session))[:50]:
            runtime.ingest(tick, tick.timestamp + timedelta(seconds=600))
        self.assertEqual(runtime.received, 50)
        self.assertEqual(runtime.processed, 0)
        self.assertEqual(runtime.engine.trades, [])
        self.assertEqual(runtime.snapshot()["live_status"][0]["feed_age_seconds"], 600)
        self.assertTrue(runtime.recoverable_fault)
        runtime.close()

    def test_seed_rejects_missing_prior_day_future_day_and_invalid_close(self):
        runtime = self.runtime()
        self.seed(runtime)
        seed = dict(runtime.engine.seeds[str(self.session.day)])
        for change in ({"cash_days": seed["cash_days"][1:]},
                       {"cash_days": seed["cash_days"][1:] + [str(self.session.day)]},
                       {"previous_close": float("nan")}, {"ranges": [float("inf")] * 20},
                       {"previous_contract": "MNQ 03-27"}):
            with self.assertRaisesRegex(ValueError, "ATR seed"):
                runtime.seed({**seed, **change})
        self.assertEqual(runtime.journal.sequence, 1)

    def test_failed_duplicate_batch_rolls_back_and_counts_each_unique_trade_once(self):
        runtime = self.runtime()
        self.seed(runtime)
        ticks = list(ticks_for(self.session))[:4]
        runtime.ingest_many([(t, t.timestamp) for t in (ticks[0], ticks[1], ticks[0], ticks[2])])
        self.assertEqual(runtime.received, 3)
        self.assertTrue(runtime.fatal)
        self.assertEqual(runtime.processed, 2)
        runtime.close()
        restored = self.runtime()
        self.assertEqual(restored.received, 3)
        self.assertTrue(restored.fatal)
        self.assertEqual(restored.processed, 2)

    def test_batched_dense_capture_and_restart_preserve_all_records(self):
        runtime = self.runtime()
        self.seed(runtime)
        first = next(ticks_for(self.session))
        ticks = [replace(first, timestamp=first.timestamp + timedelta(milliseconds=i),
                         tick_id=f"dense-{i}") for i in range(10000)]
        for offset in range(0, len(ticks), 1000):
            runtime.ingest_many([(t, t.timestamp) for t in ticks[offset:offset+1000]])
        self.assertEqual(runtime.received, 10000)
        self.assertFalse(runtime.fatal)
        runtime.close()
        restored = self.runtime()
        self.assertEqual((restored.received, restored.processed), (10000, 10000))
        self.assertEqual(restored.engine.trades, [])

    def test_disk_failure_commits_no_model_fill_and_stops_processing(self):
        runtime = self.runtime()
        self.seed(runtime)
        tick = next(ticks_for(self.session))
        with patch.object(runtime.journal, "append_many", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                runtime.ingest(tick, tick.timestamp)
        self.assertEqual((runtime.received, runtime.processed), (0, 0))
        self.assertTrue(runtime.fatal)
        self.assertIn("JOURNAL_WRITE_FAILED", runtime.error)

    def test_silence_preserves_held_position_then_fresh_quote_reconciles_and_reviews(self):
        runtime = self.real_runtime()
        for tick in ticks_for(self.session):
            runtime.ingest(tick, tick.timestamp)
            if runtime.engine.broker.position:
                break
        position = runtime.engine.broker.position
        self.assertIsNotNone(position)
        runtime.heartbeat(tick.timestamp + timedelta(seconds=100))
        self.assertTrue(runtime.recoverable_fault)
        self.assertIs(runtime.engine.broker.position, position)
        before = runtime.snapshot(tick.timestamp + timedelta(seconds=100))
        runtime.close()
        runtime = self.runtime(synthetic=False)
        self.assertEqual(runtime.snapshot(tick.timestamp + timedelta(seconds=100))["trades"], before["trades"])
        self.assertIsNotNone(runtime.engine.broker.position)
        price = position.entry_fill - 50
        fresh = Tick(tick.timestamp + timedelta(minutes=2), price, 1, tick.contract,
                     "recovery-loss", price-.25, price+.25)
        runtime.ingest(fresh, fresh.timestamp)
        self.assertIsNone(runtime.engine.broker.position)
        self.assertEqual(runtime.engine.trades[-1].exit_reason, "FAULT_EXIT")
        self.assertEqual(len(runtime.reviews[-1]["diagnostics"]), 30)
        self.assertFalse(runtime.engine.sizer.samples[-1]["eligible"])
        self.assertLess(runtime.engine.day_realized, 0)

    def test_feed_recovery_requires_fresh_complete_bars_and_retains_cash(self):
        runtime = self.real_runtime()
        ticks = list(ticks_for(self.session))
        for tick in ticks[:50]:
            runtime.ingest(tick, tick.timestamp)
        cash = runtime.engine.day_realized
        completed = len(runtime.engine.trades)
        runtime.fail("DISCONNECT", "test interruption", ticks[50].timestamp)
        for tick in ticks[55:70]:
            runtime.ingest(tick, tick.timestamp)
        self.assertTrue(runtime.recoverable_fault)
        exits = sum(t.net_ticks * .5 * t.quantity for t in runtime.engine.trades[completed:])
        self.assertEqual(runtime.engine.day_realized, cash + exits)
        first_deadline = runtime.recovery_at
        for tick in ticks[75:115]:
            runtime.ingest(tick, tick.timestamp)
        self.assertGreater(runtime.recovery_at, first_deadline)
        self.assertFalse(runtime.recoverable_fault)
        self.assertTrue(any(e["reason"] == "FEED_RECOVERED" for e in runtime.engine.events))

    def test_buffer_flushes_partial_batch_on_close_and_bounds_backlog(self):
        runtime = self.runtime()
        self.seed(runtime)
        buffer = BufferedCapture(runtime, interval=60, maximum=2)
        ticks = list(ticks_for(self.session))[:3]
        buffer.submit(ticks[0])
        buffer.submit(ticks[1])
        with self.assertRaisesRegex(ValueError, "backlog"):
            buffer.submit(ticks[2])
        buffer.close()
        self.assertEqual(runtime.received, 2)

    def test_recovery_preserves_calendar_eligibility_and_profit_target_blocks(self):
        raw = json.loads(self.calendar.read_text())
        raw["sessions"][-1]["roll_day"] = True
        self.calendar.write_text(json.dumps(raw))
        runtime = self.runtime()
        self.seed(runtime)
        ticks = list(ticks_for(self.session))
        runtime.ingest(ticks[0], ticks[0].timestamp)
        runtime.fail("DISCONNECT", "test", ticks[1].timestamp)
        for tick in ticks[1:50]:
            runtime.ingest(tick, tick.timestamp)
        self.assertFalse(runtime.recoverable_fault)
        self.assertTrue(runtime.engine.blocked)
        self.assertEqual(runtime.engine.trades, [])
        runtime.close()
        raw["sessions"][-1]["roll_day"] = False
        self.calendar.write_text(json.dumps(raw))
        runtime = self.runtime("target", config=replace(self.config, daily_profit_target_usd=5))
        self.seed(runtime)
        for tick in ticks[:50]:
            runtime.ingest(tick, tick.timestamp)
        net = runtime.engine.day_realized
        self.assertGreaterEqual(net, 5)
        runtime.fail("DISCONNECT", "test", ticks[50].timestamp)
        for tick in ticks[50:90]:
            runtime.ingest(tick, tick.timestamp)
        self.assertFalse(runtime.recoverable_fault)
        self.assertTrue(runtime.engine.blocked)
        self.assertEqual(runtime.engine.day_realized, net)

    def test_sdk_worker_subscription_buffer_capture_and_private_key_redaction(self):
        try:
            import databento as db
        except ImportError:
            self.skipTest("Optional Databento SDK is not installed")
        runtime = self.runtime()
        self.seed(runtime)
        stop = threading.Event()
        ns = int(self.session.open.timestamp()) * 10**9
        mapping = db.SymbolMappingMsg(1, 123, ns, db.SType.RAW_SYMBOL, "MNQZ6", db.SType.INSTRUMENT_ID, "123", ns, ns + 10**12)
        level = db.BidAskPair(bid_px=20000 * 10**9, ask_px=20000250000000,
                              bid_sz=10, ask_sz=10, bid_ct=1, ask_ct=1)
        record = db.MBP1Msg(1, 123, ns, 20000 * 10**9, 2, db.Action.TRADE, db.Side.BID,
                           0, ns + 1000, sequence=42, levels=level)
        subscriptions = []
        class Client:
            def subscribe(self, **kwargs):
                subscriptions.append(kwargs)
            def __iter__(self):
                yield mapping
                yield record
                stop.set()
            def terminate(self):
                pass
        fake_history = SimpleNamespace(metadata=SimpleNamespace(), timeseries=SimpleNamespace())
        worker = DatabentoWorker(runtime, "private-test-key", "MNQ DEC26", 0, stop)
        with patch("standalone_paper.datetime", wraps=datetime) as clock, patch.object(db, "Historical", return_value=fake_history), patch.object(db, "Live", return_value=Client()):
            clock.now.return_value = self.session.open
            worker.run()
        self.assertEqual(runtime.received, 1)
        self.assertEqual(subscriptions, [{"dataset": "GLBX.MDP3", "schema": "tbbo", "symbols": ["MNQZ6"], "stype_in": "raw_symbol"}])
        self.assertNotIn("private-test-key", worker.safe_error(RuntimeError("denied private-test-key")))
        self.assertNotIn("private-test-key", json.dumps(runtime.snapshot()))

    def test_provider_access_failure_can_be_repaired_on_same_persisted_account(self):
        try:
            import databento as db
        except ImportError:
            self.skipTest("Optional Databento SDK is not installed")
        runtime = self.runtime()
        self.seed(runtime)
        worker = DatabentoWorker(runtime, "private-test-key", "MNQ DEC26", 0, threading.Event())
        with patch.object(db, "Historical", side_effect=RuntimeError("API denied private-test-key")):
            worker.run()
        self.assertTrue(runtime.recoverable_fault)
        self.assertFalse(runtime.fatal)
        self.assertIn("PROVIDER_SETUP_FAILED", runtime.error)
        self.assertNotIn("private-test-key", json.dumps(runtime.snapshot()))
        runtime.close()
        restored = self.runtime()
        self.assertTrue(restored.recoverable_fault)
        self.assertFalse(restored.fatal)
        for tick in list(ticks_for(self.session))[:40]:
            restored.ingest(tick, tick.timestamp)
        self.assertFalse(restored.recoverable_fault)
        self.assertFalse(restored.fatal)


class DatabentoTests(unittest.TestCase):
    def test_explicit_contract_symbol_mapping(self):
        self.assertEqual(raw_symbol("MNQ DEC26"), "MNQZ6")
        self.assertEqual(raw_symbol("MNQ 03-27"), "MNQH7")
        for invalid in ("NQ DEC26", "MNQ", "MNQ 01-26"):
            with self.assertRaises(ValueError):
                raw_symbol(invalid)

    def test_actual_sdk_records_mapping_trade_quotes_and_undefined_prices(self):
        try:
            import databento as db
        except ImportError:
            self.skipTest("Optional Databento SDK is not installed")
        import inspect
        self.assertIn("schema", inspect.signature(db.Live.subscribe).parameters)
        decoder = TradeDecoder("MNQ 12-26")
        ns = int(datetime(2026, 10, 7, 18, tzinfo=ET).timestamp()) * 10**9
        mapping = db.SymbolMappingMsg(1, 123, ns, db.SType.RAW_SYMBOL, "MNQZ6", db.SType.INSTRUMENT_ID, "123", ns, ns + 10**12)
        self.assertIsNone(decoder.decode(mapping))
        level = db.BidAskPair(bid_px=30000 * 10**9, ask_px=30000250000000,
                              bid_sz=10, ask_sz=10, bid_ct=1, ask_ct=1)
        record = db.MBP1Msg(1, 123, ns, 30000 * 10**9, 2, db.Action.TRADE, db.Side.BID, 0, ns + 1000, sequence=42, levels=level)
        tick = decoder.decode(record)
        self.assertEqual((tick.contract, tick.price, tick.volume, tick.bid, tick.ask), ("MNQ 12-26", 30000, 2, 30000, 30000.25))
        self.assertEqual(decoder.decode(record).tick_id, tick.tick_id)
        with self.assertRaisesRegex(ValueError, "mapping"):
            TradeDecoder("MNQ 12-26").decode(record)
        empty_level = db.BidAskPair(bid_px=2**63-1, ask_px=30000250000000,
                                   bid_sz=0, ask_sz=10, bid_ct=0, ask_ct=1)
        undefined = db.MBP1Msg(1, 123, ns, 30000 * 10**9, 2, db.Action.TRADE, db.Side.BID,
                              0, ns + 1000, sequence=43, levels=empty_level)
        with self.assertRaisesRegex(ValueError, "undefined price"):
            decoder.decode(undefined)

    def test_cash_minute_coverage_and_strict_prior_true_range_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            calendar = fixture(Path(directory) / "calendar.json", 24)
            sessions = list(calendar.sessions.values())
            summaries = []
            for session in sessions[-22:-1]:
                bars = [MinuteBar((session.cash_open + timedelta(minutes=i)).isoformat(), 20000, 20010, 19990, 20000, 1)
                        for i in range(390)]
                summaries.append(validate_cash_bars(session, bars))
                with self.assertRaisesRegex(ValueError, "expected"):
                    validate_cash_bars(session, bars[:-1])
            seed = atr_seed(calendar, sessions[-1].day, summaries)
            self.assertEqual(seed["ranges"], [20] * 20)
            with self.assertRaisesRegex(ValueError, "immediately preceding"):
                atr_seed(calendar, sessions[-2].day, summaries)

    def test_historical_budget_prevents_download(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            calendar = fixture(Path(directory) / "calendar.json", 24)
            called = []
            client = SimpleNamespace(metadata=SimpleNamespace(get_cost=lambda **kwargs: .1),
                                     timeseries=SimpleNamespace(get_range=lambda **kwargs: called.append(kwargs)))
            loader = HistoryLoader(client, calendar, "MNQ DEC26", Path(directory) / "cache", 0)
            with self.assertRaisesRegex(ValueError, "budget"):
                loader.seed(list(calendar.sessions)[-1], datetime(2027, 1, 1, tzinfo=timezone.utc))
            self.assertEqual(called, [])

    def test_verified_history_cache_reuse_and_corruption_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calendar = fixture(root / "calendar.json", 24)
            target = list(calendar.sessions)[-1]
            calls = []
            class OHLCVMsg(SimpleNamespace):
                pass
            def download(**params):
                calls.append(params)
                start = params["start"]
                return [OHLCVMsg(ts_event=int((start + timedelta(minutes=i)).timestamp()) * 10**9,
                                 open=20000*10**9, high=20010*10**9, low=19990*10**9,
                                 close=20000*10**9, volume=1) for i in range(390)]
            client = SimpleNamespace(metadata=SimpleNamespace(get_cost=lambda **kwargs: .001),
                                     timeseries=SimpleNamespace(get_range=download))
            now = datetime(2027, 1, 1, tzinfo=timezone.utc)
            loader = HistoryLoader(client, calendar, "MNQ DEC26", root / "cache", .03)
            seed = loader.seed(target, now)
            self.assertEqual(len(calls), 21)
            self.assertAlmostEqual(loader.estimated_cost, .021)
            no_network = SimpleNamespace(metadata=SimpleNamespace(get_cost=lambda **kwargs: self.fail("Cache should not need metadata")),
                                         timeseries=SimpleNamespace(get_range=lambda **kwargs: self.fail("Cache should not download")))
            cached = HistoryLoader(no_network, calendar, "MNQ DEC26", root / "cache", 0)
            self.assertEqual(cached.seed(target, now), seed)
            path = sorted((root / "cache").glob("*.json"))[0]
            envelope = json.loads(path.read_text())
            envelope["bars"][0]["close"] += .25
            path.write_text(json.dumps(envelope))
            with self.assertRaisesRegex(ValueError, "cache integrity"):
                cached.seed(target, now)

    def test_historical_cancel_makes_no_cost_or_data_request(self):
        with tempfile.TemporaryDirectory() as directory:
            calendar = fixture(Path(directory) / "calendar.json", 24)
            stop = threading.Event()
            stop.set()
            loader = HistoryLoader(None, calendar, "MNQ DEC26", Path(directory) / "cache", 1, stop)
            with self.assertRaises(InterruptedError):
                loader.seed(list(calendar.sessions)[-1], datetime(2027, 1, 1, tzinfo=timezone.utc))
