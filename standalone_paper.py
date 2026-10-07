#!/usr/bin/env python3
"""Run the standalone MNQ paper service and its local browser dashboard."""

import argparse
from collections import deque
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
import errno
import getpass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import webbrowser

from bot.data import ET
from bot.databento_feed import DATASET, FeedDataError, HistoryLoader, TradeDecoder, raw_symbol
from bot.dashboard import html_document
from bot.fullsession import futures_day
from bot.models import Config, Tick
from bot.standalone import PaperRuntime

PROJECT = Path(__file__).resolve().parent
CALENDAR = PROJECT / "ninjatrader/calendars/mnq-dec26-full-session-2026-10-07-30/calendar.json"


def create_server(runtime, port=8766):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            route = self.path.split("?", 1)[0]
            if route == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if route not in ("/", "/diagnostics.json"):
                self.send_error(404)
                return
            payload = runtime.snapshot()
            body = (html_document(payload) if route == "/" else json.dumps(payload, allow_nan=False)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8" if route == "/" else "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if route == "/diagnostics.json":
                self.send_header("Content-Disposition", 'attachment; filename="mnq-standalone-diagnostics.json"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    try:
        return ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE and getattr(exc, "winerror", None) != 10048:
            raise
        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def demo_calendar(path):
    today = datetime.now(ET).date()
    days = []
    cursor = today - timedelta(days=45)
    while cursor <= today + timedelta(days=12):
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    rows = [{"date": str(day), "open": datetime.combine(day, time(9, 30), ET).isoformat(),
             "close": datetime.combine(day, time(16), ET).isoformat(), "contract": "MNQ 12-26",
             "roll_day": False, "trade_enabled": True, "news": {"flags": [], "releases": []},
             "globex": {"open": datetime.combine(day - timedelta(days=1), time(18), ET).isoformat(),
                        "close": datetime.combine(day, time(17), ET).isoformat(), "breaks": []}}
            for day in days]
    content = json.dumps({"version": 1, "source": "SYNTHETIC STANDALONE DEMO", "synthetic": True,
                          "news_as_of": rows[0]["open"], "sessions": rows})
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content)
    return path


def demo_worker(runtime, stop, interval=.02):
    runtime.status("DEMO", "Synthetic prices, accelerated clock; no Databento connection")
    sessions = [s for day, s in sorted(runtime.calendar.sessions.items()) if day > datetime.now(ET).date()][:4]
    bars = [(0, 3, -1, 2), (2, 5, 1, 4), (4, 7, 3, 6), (6, 9, 5, 8), (8, 9, 6.5, 7), (7, 13, 6.5, 12)]
    for session in sessions:
        runtime.seed({"target_day": str(session.day), "ranges": [150] * 20,
                      "previous_close": 30000, "previous_contract": session.contract,
                      "cash_days": [str(d) for d in runtime.calendar.sessions if d < session.day][-21:],
                      "source": "SYNTHETIC DEMO"})
        for minute in range(int((session.close - session.open).total_seconds() // 60)):
            if stop.is_set():
                return
            cycle, index = divmod(minute // 5, 6)
            inside = minute % 5
            o, high, low, close = bars[index]
            if index == 0 and cycle and cycle % 4 == 0:
                low = -10  # Exercise losses as well as wins.
            value = 30000 + cycle * 12 + (o, high, low, close, close)[inside]
            timestamp = session.open + timedelta(minutes=minute)
            tick = Tick(timestamp, value, 1, session.contract, f"demo-{session.day}-{minute}", value - .25, value + .25)
            if runtime.latest_tick and tick.timestamp <= runtime.latest_tick.timestamp:
                continue
            runtime.ingest(tick, timestamp)
            if stop.wait(interval):
                return
    runtime.status("DEMO_COMPLETE", "Synthetic fixture finished; saved results remain visible")


class DatabentoWorker:
    def __init__(self, runtime, key, contract, history_budget, stop):
        self.runtime, self.key, self.contract = runtime, key, contract
        self.history_budget, self.stop = history_budget, stop
        self.client = None
        self.refresh_stop = threading.Event()
        self.refresher = None

    def safe_error(self, exc):
        return str(exc).replace(self.key, "[API key redacted]")

    def run(self):
        try:
            import databento as db
            now = datetime.now(timezone.utc)
            history = db.Historical(key=self.key)
            history.metadata.TIMEOUT = history.timeseries.TIMEOUT = 10
            loader = HistoryLoader(history, self.runtime.calendar, self.contract,
                                   self.runtime.directory.parent / "history-cache", self.history_budget, self.stop)
            self.runtime.status("HISTORY", "Checking real cash-minute history and the configured download cap")
            target = futures_day(now)
            if str(target) not in self.runtime.engine.seeds:
                self.runtime.seed(loader.seed(target, now))
            if self.stop.is_set():
                return
            def refresh_history():
                retry_after = {}
                while not self.refresh_stop.wait(30) and not self.stop.is_set():
                    wall = datetime.now(timezone.utc)
                    pending = [day for day in sorted(self.runtime.calendar.sessions)
                               if str(day) not in self.runtime.engine.seeds
                               and (day not in retry_after or wall >= retry_after[day])
                               and day >= futures_day(wall)]
                    for day in pending[:1]:
                        prior = [s for d, s in sorted(self.runtime.calendar.sessions.items()) if d < day]
                        if prior and prior[-1].cash_close + timedelta(minutes=2) <= wall:
                            retry_after[day] = wall + timedelta(minutes=10)
                            try:
                                seed = loader.seed(day, wall)
                                if not self.stop.is_set() and not self.refresh_stop.is_set():
                                    self.runtime.seed(seed)
                            except Exception as exc:
                                if not self.stop.is_set() and not self.refresh_stop.is_set():
                                    self.runtime.status("HISTORY_REFRESH_FAILED", self.safe_error(exc))
            self.refresher = threading.Thread(target=refresh_history, daemon=True)
            self.refresher.start()
            if self.runtime.received:
                self.runtime.fail("DISCONNECT", "Restored journal requires fresh quotes and a new six-bar capture segment")
            backoff = 5
            while not self.stop.is_set():
                self.runtime.status("CONNECTING", "Requesting GLBX.MDP3 TBBO for " + raw_symbol(self.contract))
                decoder = TradeDecoder(self.contract)
                self.client = db.Live(key=self.key, heartbeat_interval_s=10, reconnect_policy="none")
                capture = BufferedCapture(self.runtime)
                try:
                    self.client.subscribe(dataset=DATASET, schema="tbbo", symbols=[decoder.symbol], stype_in="raw_symbol")
                    for record in self.client:
                        if self.stop.is_set():
                            break
                        tick = decoder.decode(record)
                        if type(record).__name__ == "SymbolMappingMsg":
                            self.runtime.status("CONNECTED", "Verified contract mapping; awaiting fresh trade quotes")
                        if tick is not None:
                            capture.submit(tick)
                            backoff = 5
                except ValueError:
                    raise  # Mapping/price/schema faults need repair, not a retry.
                except Exception as exc:
                    if not self.stop.is_set():
                        self.runtime.fail("DISCONNECT", self.safe_error(exc))
                finally:
                    self.terminate()
                    capture.close()
                if not self.stop.is_set():
                    self.runtime.fail("DISCONNECT", "Stream interrupted; cash and positions retained")
                    self.runtime.status("RECONNECTING", f"Retrying data connection in {backoff} seconds; entries paused")
                    if self.stop.wait(backoff):
                        break
                    backoff = min(60, backoff * 2)
        except Exception as exc:
            if not self.stop.is_set():
                self.runtime.fail("DATA_QUALITY" if isinstance(exc, FeedDataError) else "PROVIDER_SETUP_FAILED", self.safe_error(exc))
                self.runtime.status("ERROR", "Read the provider error in the dashboard; recording cannot continue until access is repaired")
        finally:
            self.refresh_stop.set()
            self.terminate()
            if self.refresher:
                self.refresher.join(timeout=12)

    def terminate(self):
        if self.client is not None:
            try:
                self.client.terminate()
            except (ValueError, RuntimeError):
                pass


class BufferedCapture:
    """Bounded tick capture with durable commits at most 100 ms apart."""

    def __init__(self, runtime, interval=.1, maximum=10000):
        self.runtime, self.interval, self.maximum = runtime, interval, maximum
        self.pending = deque()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def submit(self, tick):
        with self.lock:
            if self.error:
                raise RuntimeError("Paper capture failed: " + str(self.error))
            if len(self.pending) >= self.maximum:
                raise ValueError("Paper capture backlog exceeded its bound; stop and inspect computer performance")
            self.pending.append(tick)

    def flush(self):
        with self.lock:
            ticks = list(self.pending)
            self.pending.clear()
        # Processing time includes capture backlog in the freshness test.
        now = datetime.now(timezone.utc)
        self.runtime.ingest_many([(tick, now) for tick in ticks])

    def run(self):
        try:
            while not self.stop.wait(self.interval):
                self.flush()
            self.flush()
        except Exception as exc:
            self.error = exc

    def close(self):
        self.stop.set()
        self.thread.join()
        if self.error:
            raise RuntimeError("Paper capture failed: " + str(self.error))


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--demo", action="store_true", help="Accelerated synthetic demo; never uses a data API key")
    p.add_argument("--config", type=Path, default=PROJECT / "config.adaptive-paper.json")
    p.add_argument("--calendar", type=Path, default=CALENDAR)
    p.add_argument("--contract", default="MNQ 12-26")
    p.add_argument("--account", default="paper", help="Local experiment/account label")
    p.add_argument("--stage", choices=("funded", "evaluation"), default=os.environ.get("MNQ_PAPER_STAGE", "funded"))
    p.add_argument("--history-budget", type=float, default=float(os.environ.get("MNQ_HISTORY_BUDGET", "0")),
                   help="Maximum estimated historical-download dollars for this launch; default cache-only")
    p.add_argument("--data-dir", type=Path, default=PROJECT / "runs/standalone")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--no-browser", action="store_true")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if not args.account or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in args.account):
        raise ValueError("Account label must contain only letters, numbers, hyphens or underscores")
    if not 1 <= args.port <= 65535:
        raise ValueError("Port must be 1-65535")
    if args.stage not in ("funded", "evaluation"):
        raise ValueError("Paper account stage must be funded or evaluation")
    config = replace(Config.load(args.config), daily_profit_target_usd=750 if args.stage == "evaluation" else 0)
    account = "demo-" + args.account if args.demo else args.account
    folder = args.data_dir / (account + "-" + raw_symbol(args.contract))
    calendar = demo_calendar(folder / "calendar.json") if args.demo else args.calendar
    runtime = PaperRuntime(config, calendar, folder, account, synthetic=args.demo)
    stop = threading.Event()
    provider = None
    with create_server(runtime, args.port) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print("Standalone MNQ paper service · " + ("SYNTHETIC DEMO" if args.demo else "Databento market data"), flush=True)
        print(f"Browser dashboard: {url}", flush=True)
        print(f"Persistent paper journal: {folder / 'paper.sqlite3'}", flush=True)
        print("Keep this window open. Ctrl+C stops the service; the journal retains positions, cash and learning.", flush=True)
        http_thread = threading.Thread(target=server.serve_forever, daemon=True)
        http_thread.start()
        worker = None
        try:
            if not args.no_browser:
                webbrowser.open(url)
            if args.demo:
                worker = threading.Thread(target=demo_worker, args=(runtime, stop), daemon=True)
            else:
                key = os.environ.get("DATABENTO_API_KEY") or getpass.getpass("Databento API key (hidden; never saved): ")
                if not key or not key.strip():
                    raise ValueError("A local Databento API key is required; use --demo to test without one")
                provider = DatabentoWorker(runtime, key.strip(), args.contract, args.history_budget, stop)
                worker = threading.Thread(target=provider.run, daemon=True)
            worker.start()
            while not stop.wait(1):
                runtime.heartbeat()
        except KeyboardInterrupt:
            print("Stopping paper service. Saved records are preserved.")
        finally:
            stop.set()
            if provider:
                provider.refresh_stop.set()
                provider.terminate()
            if worker:
                worker.join()
            server.shutdown()
            http_thread.join(timeout=5)
            runtime.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("Standalone setup failed: " + str(exc))
        raise SystemExit(1)
