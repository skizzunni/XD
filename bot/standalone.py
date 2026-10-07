"""Durable standalone paper execution and read-only browser snapshots."""

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import math
import sqlite3
import threading

from .data import Calendar, checksum
from .fullsession import FullSessionCalendar, FullSessionEngine, futures_day
from .models import Tick
from .review import diagnose


def encoded(value):
    def convert(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError("Unsupported journal/snapshot type: " + type(item).__name__)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=convert)


def core_fingerprint():
    root = Path(__file__).parent
    names = ("engine.py", "fullsession.py", "models.py", "broker.py", "strategy.py",
             "learning.py", "sizing.py", "contracts.py", "standalone.py", "databento_feed.py")
    return hashlib.sha256(b"".join((root / name).read_bytes() for name in names)).hexdigest()


class Journal:
    """Commit inputs before processing; replay restores cash, positions and learning."""

    def __init__(self, path, metadata):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.owner = path.with_suffix(".owner").open("a+b")
        self.connection = None
        try:
            if __import__("os").name == "nt":
                import msvcrt
                self.owner.seek(0)
                self.owner.write(b"0")
                self.owner.flush()
                self.owner.seek(0)
                msvcrt.locking(self.owner.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.connection = sqlite3.connect(path, check_same_thread=False)
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("CREATE TABLE IF NOT EXISTS metadata (value TEXT NOT NULL)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS operations (sequence INTEGER PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL, digest TEXT NOT NULL, tick_id TEXT UNIQUE)")
            existing = self.connection.execute("SELECT value FROM metadata").fetchall()
            if existing and (len(existing) != 1 or existing[0][0] != encoded(metadata)):
                raise ValueError("Existing paper journal uses different strategy/config/calendar. Preserve it; select a separate account label for a different experiment.")
            if not existing:
                self.connection.execute("INSERT INTO metadata VALUES (?)", (encoded(metadata),))
                self.connection.commit()
            if self.connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Paper journal integrity check failed")
            self.anchor = hashlib.sha256(encoded(metadata).encode()).hexdigest()
            self.last_digest = self.anchor
            self.sequence = 0
        except BaseException:
            self.close()
            raise

    def replay(self):
        for sequence, kind, body, digest in self.connection.execute("SELECT sequence,kind,body,digest FROM operations ORDER BY sequence"):
            expected = hashlib.sha256((self.last_digest + kind + body).encode()).hexdigest()
            if sequence != self.sequence + 1 or digest != expected:
                raise ValueError("Paper journal sequence/checksum failed; existing records are preserved")
            self.sequence, self.last_digest = sequence, digest
            yield kind, json.loads(body)

    def append(self, kind, body, tick_id=None):
        self.append_many([(kind, body, tick_id)])

    def append_many(self, rows):
        values, digest, sequence = [], self.last_digest, self.sequence
        for kind, body, tick_id in rows:
            raw = encoded(body)
            digest = hashlib.sha256((digest + kind + raw).encode()).hexdigest()
            sequence += 1
            values.append((sequence, kind, raw, digest, tick_id))
        try:
            self.connection.executemany("INSERT INTO operations VALUES (?,?,?,?,?)", values)
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        self.sequence, self.last_digest = sequence, digest

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.owner is not None:
            self.owner.close()
            self.owner = None


class CommittedTickIndex:
    def register(self, key):
        # Journal.tick_id is UNIQUE and committed before Engine.process. Restore
        # validates that journal too, avoiding an unbounded in-memory tick set.
        return True


class StandaloneEngine(FullSessionEngine):
    def __init__(self, *args, **kwargs):
        self.seeds = {}
        self.capture_starts = {}
        super().__init__(*args, tick_index=CommittedTickIndex(), **kwargs)

    def _start_session(self, session):
        seed = self.seeds.get(str(session.day))
        if seed:
            self.daily_ranges = list(seed["ranges"])
            self.previous_close = seed["previous_close"]
            self.previous_contract = seed["previous_contract"]
        super()._start_session(session)

    def coverage_open(self, session):
        return max(super().coverage_open(session), self.capture_starts.get(session.day, session.open))

    def _close_bar(self):
        if (self.current_bar is not None and self.session is not None
                and self.current_bar.start < self.capture_starts.get(self.session.day, self.session.open)):
            self.current_bar = None  # An attachment/recovery fragment is not a complete entry bar.
            return
        super()._close_bar()


class PaperRuntime:
    def __init__(self, config, calendar_path, directory, account="paper", synthetic=False):
        if config.strategy != "R2":
            raise ValueError("Standalone execution currently requires R2")
        self.lock = threading.RLock()
        self.config, self.account = config, account
        self.directory = Path(directory)
        self.calendar = FullSessionCalendar(Calendar(calendar_path))
        if self.calendar.synthetic and not synthetic:
            raise ValueError("Live paper execution requires a reviewed real calendar")
        self.synthetic = synthetic
        self.engine = StandaloneEngine(config, self.calendar)
        self.engine.calendar_checksum = checksum(calendar_path)
        self.journal = Journal(self.directory / "paper.sqlite3", {
            "format": 1, "account": account, "config": config.to_dict(),
            "calendar_sha256": self.engine.calendar_checksum,
            "core_sha256": core_fingerprint(), "synthetic": synthetic})
        self.received = self.processed = 0
        self.latest_tick = self.latest_received = None
        self.connection = "STARTING"
        self.error = None
        self.recovery_at = None
        self.recoverable_fault = False
        self.fatal = False
        self.last_checked = None
        self.last_sizing = {}
        self.reviews = []
        try:
            for kind, body in self.journal.replay():
                self._apply(kind, body)
        except BaseException:
            self.close()
            raise

    def record(self, kind, body, tick_id=None):
        with self.lock:
            try:
                self.journal.append(kind, body, tick_id)
            except sqlite3.IntegrityError:
                self.fail("DUPLICATE_TICK", "Duplicate feed trade was rejected", datetime.fromisoformat(body["received_at"]))
                return
            except Exception:
                self.fatal, self.error = True, "JOURNAL_WRITE_FAILED: paper processing stopped; preserve the journal"
                self.engine.blocked = True
                raise
            self._apply(kind, body)

    def seed(self, seed):
        target = next((d for d in self.calendar.sessions if str(d) == seed.get("target_day")), None)
        prior = [s for d, s in sorted(self.calendar.sessions.items()) if target and d < target][-21:]
        if (len(seed.get("ranges", [])) != 20 or not seed.get("cash_days")
                or target is None or len(prior) != 21
                or seed["cash_days"] != [str(s.day) for s in prior]
                or seed.get("previous_contract") != prior[-1].contract
                or not isinstance(seed.get("previous_close"), (int, float))
                or not math.isfinite(seed["previous_close"]) or seed["previous_close"] <= 0
                or any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in seed["ranges"])
                or sum(seed["ranges"]) <= 0):
            raise ValueError("Invalid verified ATR seed")
        encoded(seed)  # Reject non-finite input before committing it.
        self.record("SEED", seed)

    def ingest(self, tick, received_at=None):
        self.ingest_many([(tick, received_at or datetime.now(timezone.utc))])

    def ingest_many(self, items):
        rows = []
        for tick, received_at in items:
            if received_at.tzinfo is None or received_at.utcoffset() is None:
                raise ValueError("Receipt time must be timezone-aware")
            tick.validate()
            body = asdict(tick)
            body["timestamp"] = tick.timestamp.isoformat()
            body["received_at"] = received_at.isoformat()
            rows.append(("TICK", body, tick.tick_id))
        if not rows:
            return
        with self.lock:
            try:
                self.journal.append_many(rows)
            except sqlite3.IntegrityError:
                # The whole failed batch rolled back. Apply each unique input
                # once; the duplicate creates a fault rather than extra fills.
                for kind, body, tick_id in rows:
                    self.record(kind, body, tick_id)
                return
            except Exception:
                self.fatal, self.error = True, "JOURNAL_WRITE_FAILED: paper processing stopped; preserve the journal"
                self.engine.blocked = True
                raise
            for kind, body, tick_id in rows:
                self._apply(kind, body)

    def fail(self, reason, detail, now=None):
        now = now or datetime.now(timezone.utc)
        self.record("FAULT", {"reason": reason, "detail": detail, "timestamp": now.isoformat()})

    def status(self, state, detail="", now=None):
        self.record("STATUS", {"state": state, "detail": detail,
                               "timestamp": (now or datetime.now(timezone.utc)).isoformat()})

    def _apply(self, kind, body):
        e = self.engine
        if kind == "SEED":
            e.seeds[body["target_day"]] = body
            if e.session and str(e.session.day) == body["target_day"] and not e.broker.position:
                e.daily_ranges = list(body["ranges"])
                e.previous_close, e.previous_contract = body["previous_close"], body["previous_contract"]
                e.atr = sum(e.daily_ranges) / 20
                if not self.recoverable_fault and not self.fatal and e.session.eligibility == "ELIGIBLE":
                    e.blocked = e.day_realized <= -self.config.session_loss_budget_usd
                    if self.config.daily_profit_target_usd and e.day_realized >= self.config.daily_profit_target_usd:
                        e.blocked = True
            return
        if kind == "STATUS":
            self.connection = body["state"]
            e.log(datetime.fromisoformat(body["timestamp"]), "feed", "FEED_STATUS", **body)
            return
        if kind == "FAULT":
            now = datetime.fromisoformat(body["timestamp"])
            e.blocked = e.quality_fault = True
            self.error = body["reason"] + ": " + body["detail"]
            self.recoverable_fault = body["reason"] in {"STALE_FEED", "DISCONNECT", "CLOCK_SKEW", "PROVIDER_SETUP_FAILED"}
            self.fatal = self.fatal or not self.recoverable_fault
            self.recovery_at = None
            e.log(now, "feed", body["reason"], detail=body["detail"])
            return
        if kind != "TICK":
            raise ValueError("Unknown journal operation")
        received_at = datetime.fromisoformat(body["received_at"])
        tick = Tick(datetime.fromisoformat(body["timestamp"]), body["price"], body["volume"],
                    body["contract"], body["tick_id"], body["bid"], body["ask"])
        self.received += 1
        self.latest_tick, self.latest_received = tick, received_at
        lag = (received_at - tick.timestamp).total_seconds()
        if not self.synthetic and (lag > self.config.max_tick_gap_seconds or lag < -5):
            e.blocked = e.quality_fault = True
            self.recoverable_fault = True
            self.recovery_at = None
            self.error = "STALE_FEED" if lag > 0 else "CLOCK_SKEW"
            # Capture delayed data, but never execute a paper fill on it.
            return
        if self.fatal:
            return
        day = futures_day(tick.timestamp)
        e.capture_starts.setdefault(day, tick.timestamp)
        if (e.session and e.session.day == day and e.session_last
                and e.gap_seconds(e.session_last.timestamp, tick.timestamp, e.session) > self.config.max_tick_gap_seconds):
            self.recoverable_fault = True
            self.recovery_at = None
            e.blocked = e.quality_fault = True
            self.error = "DATA_GAP: rebuilding a fresh capture segment"
        previous_events, previous_trades = len(e.events), len(e.trades)
        if self.recoverable_fault:
            if self.recovery_at is None:
                start = tick.timestamp.replace(second=0, microsecond=0)
                start -= timedelta(minutes=start.minute % 5)
                self.recovery_at = start + timedelta(minutes=35)
                e.capture_starts[day] = tick.timestamp
                e.bars, e.current_bar, e.rolling_signal = [], None, None
            if e.broker.position:
                # Reconcile a held simulated position only at a newly received,
                # validated, executable quote; do not invent a fill during silence.
                e.fault(tick, "FAULT_EXIT")
            e.blocked = True
            if tick.timestamp >= self.recovery_at and e.atr and e.atr > 0:
                self.recoverable_fault, self.error = False, None
                e.blocked = (e.session is None or e.session.eligibility != "ELIGIBLE"
                             or e.day_realized <= -self.config.session_loss_budget_usd
                             or bool(self.config.daily_profit_target_usd and e.day_realized >= self.config.daily_profit_target_usd))
                e.log(tick, "feed", "FEED_RECOVERED", detail="Fresh capture rebuilt six complete five-minute bars; cash risk retained")
        try:
            e.process(tick)
        except (ValueError, RuntimeError) as exc:
            self.fatal, self.error = True, "ENGINE_ERROR: " + str(exc)
            e.blocked = True
            e.log(received_at, "runtime", "ENGINE_ERROR", detail=str(exc))
        self.processed += 1
        for event in e.events[previous_events:]:
            if event["reason"] == "SIZING_DECISION":
                self.last_sizing[event["profile"]] = event
        for trade, audit in zip(e.trades[previous_trades:], e.trade_audits[previous_trades:]):
            if trade.net_ticks < 0:
                self.reviews.append(diagnose(trade, audit, e.events, self.config))

    def heartbeat(self, now=None):
        now = now or datetime.now(timezone.utc)
        with self.lock:
            self.last_checked = now
            tick = self.latest_tick
            session = self.calendar.sessions.get(futures_day(now))
            if (not self.synthetic and tick is not None and session and session.market_open(now)
                    and (now - tick.timestamp).total_seconds() > self.config.max_tick_gap_seconds
                    and not self.recoverable_fault and not self.fatal):
                self.fail("STALE_FEED", "No fresh trade inside the reviewed open session", now)

    def snapshot(self, now=None):
        now = now or datetime.now(timezone.utc)
        with self.lock:
            e, tick, position = self.engine, self.latest_tick, self.engine.broker.position
            market_time = tick.timestamp if self.synthetic and tick else now
            day = futures_day(market_time)
            session = self.calendar.sessions.get(day)
            phase = ("NO_CALENDAR_SESSION" if session is None else "CME_CLOSED" if not session.market_open(market_time)
                     else "BLOCKED" if self.fatal or self.recoverable_fault else "POSITION_OPEN" if position
                     else "WAITING_HISTORY" if not e.atr else "BLOCKED" if e.blocked
                     else "NEWS_PAUSE" if session.news_paused(market_time)
                     else "WAITING_R2_BARS" if len(e.bars) < 6 else "SCANNING")
            diagnostic = (self.error or
                          ("No reviewed session covers this time; update the frozen calendar." if session is None else
                           "Verified prior cash-minute history is required before entries." if not e.atr else
                           "Session loss budget reached; recording continues until the next futures day." if e.day_realized <= -self.config.session_loss_budget_usd else
                           "Evaluation profit target reached; recording continues until the next futures day." if self.config.daily_profit_target_usd and e.day_realized >= self.config.daily_profit_target_usd else
                           "Calendar eligibility: " + session.eligibility if session.eligibility != "ELIGIBLE" else
                           "Six complete fresh five-minute bars are required; an attachment fragment is discarded." if len(e.bars) < 6 else
                           "R2 checks closed bars, trend quality, news, quoted spread, slippage and remaining cash risk before each entry. Rejected setups remain in the event ledger."))
            mark = tick.bid if position and position.direction > 0 else tick.ask if position else None
            unrealized = ((position.direction * (mark - position.entry_fill) * 2 - self.config.round_turn_fees_usd
                           - self.config.slippage_ticks_per_side * .5) * position.quantity) if position and mark else 0
            live = [] if tick is None else [{"account": self.account, "strategy": "R2", "timestamp": tick.timestamp.isoformat(),
                "price": tick.price, "open_qty": position.quantity if position else 0, "unrealized_usd": unrealized,
                "day_net_usd": e.day_realized, "blocked": e.blocked or self.recoverable_fault or self.fatal,
                "phase": phase, "exit_profile": self.config.exit_profile, "regime": "OVERNIGHT" if e.overnight(tick.timestamp) else "RTH",
                "futures_day": str(day), "received_at_et": self.latest_received.isoformat(),
                "feed_age_seconds": round((self.latest_received - tick.timestamp).total_seconds(), 3),
                "max_tick_gap_seconds": self.config.max_tick_gap_seconds, "stop_price": position.stop if position else None,
                "synthetic": self.synthetic}]
            trades = []
            for i, trade in enumerate(e.trades):
                row = asdict(trade)
                row.update(id=str(i + 1), account=self.account, stage="evaluation" if self.config.daily_profit_target_usd else "funded",
                           net_usd=trade.net_ticks * .5 * trade.quantity, exit_profile=self.config.exit_profile)
                trades.append(row)
            learners, sizing = [], []
            for night, learner in ((False, e.learner), (True, e.overnight_learner)):
                regime = "OVERNIGHT" if night else "RTH"
                for direction in (1, -1):
                    learners.append({"account": self.account, "regime": regime, "exit_profile": self.config.exit_profile,
                                     **learner.state(direction, market_time)})
                if self.config.adaptive_sizing:
                    selected = self.last_sizing.get(self.config.exit_profile + ":" + regime, {})
                    sizing.append({"account": self.account, "regime": regime, "exit_profile": self.config.exit_profile,
                                   "adaptive_sizing": True,
                                   "selected_contracts": selected.get("selected_contracts"),
                                   "remaining_budget_usd": selected.get("remaining_budget_usd"),
                                   **e.sizer.state(self.config.exit_profile + ":" + regime, market_time)})
            events = [{**event, "account": self.account,
                       "details": encoded({k: v for k, v in event.items() if k not in {"timestamp", "reason", "account"}})}
                      for event in e.events[-1500:]]
            payload = {"source": "Standalone Python paper · " + ("SYNTHETIC DEMO" if self.synthetic else "Databento CME"),
                    "synthetic": self.synthetic, "trades": trades, "loss_reviews": self.reviews,
                    "pending_positions": [], "live_status": live, "learning_status": learners,
                    "sizing_status": sizing, "events": events, "entry_checks": [],
                    "runtime": {"account": self.account, "connection": self.connection, "received_ticks": self.received,
                                "processed_ticks": self.processed, "last_received_at": self.latest_received.isoformat() if self.latest_received else None,
                                "error": self.error, "phase": phase, "atr20": e.atr, "completed_atr_sessions": min(20, len(e.daily_ranges)),
                                "completed_entry_bars": len(e.bars), "session_net_usd": e.day_realized,
                                "loss_budget_usd": self.config.session_loss_budget_usd,
                                "profit_target_usd": self.config.daily_profit_target_usd,
                                "diagnostic": diagnostic,
                                "recovery_at": self.recovery_at.isoformat() if self.recovery_at else None,
                                "journal_sequence": self.journal.sequence, "snapshot_at": now.isoformat()},
                    "note": "Synthetic prices test the program only; they cannot establish profit." if self.synthetic
                    else "Standalone simulated fills on Databento quotes. Fees/slippage are configured estimates; size impact is not modeled. No brokerage orders are sent."}
            return json.loads(encoded(payload))

    def close(self):
        self.journal.close()
