"""R2 paper experiment: reviewed full CME sessions with separate overnight rules."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from .contracts import same_contract
from .data import ET, Session
from .engine import Engine
from .learning import QualityLearner


def futures_day(timestamp):
    eastern = timestamp.astimezone(ET)
    return eastern.date() + timedelta(days=eastern.time() >= time(18))


@dataclass(frozen=True)
class FullSession(Session):
    cash_open: datetime | None = None
    cash_close: datetime | None = None

    @property
    def eligibility(self):
        if self.cash_close.time() != time(16):
            return "EARLY_CLOSE"
        if self.roll_day:
            return "ROLL_DAY"
        if not self.contract:
            return "UNRESOLVED_CONTRACT"
        return "ELIGIBLE" if self.trade_enabled else "WARMUP_ONLY"

    def market_open(self, timestamp):
        return self.open <= timestamp < self.close and not any(a <= timestamp < b for a, b in self.globex_breaks)


class FullSessionCalendar:
    def __init__(self, cash_calendar):
        self.synthetic = cash_calendar.synthetic
        self.sessions = {}
        for day, s in cash_calendar.sessions.items():
            if s.globex_open is None or s.globex_close is None:
                raise ValueError("R2 requires reviewed Globex open/close times for every calendar row")
            self.sessions[day] = FullSession(
                day, s.globex_open, s.globex_close, s.contract, s.roll_day,
                s.news_flags, s.releases, s.trade_enabled,
                s.globex_open, s.globex_close, s.globex_breaks, s.open, s.close,
            )

    def for_tick(self, tick):
        day = futures_day(tick.timestamp)
        if day not in self.sessions:
            raise ValueError(f"UNRESOLVED_SESSION: R2 futures date {day} is absent from the reviewed calendar")
        return self.sessions[day]


class FullSessionEngine(Engine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.overnight_learner = QualityLearner(
            max(self.config.overnight_min_efficiency, self.config.rolling_min_efficiency),
            self.config.adaptive_quality,
        )
        self.cash_first = self.cash_last = None

    @staticmethod
    def overnight(timestamp):
        return not time(9, 30) <= timestamp.astimezone(ET).time() < time(16)

    def rolling_rules(self, timestamp):
        night = self.overnight(timestamp)
        closing = self.session.close-timedelta(minutes=15)
        for start, end in self.session.globex_breaks:
            if end > timestamp:
                closing = min(closing, start-timedelta(minutes=5))
        return {
            "efficiency": max(self.config.rolling_min_efficiency, self.config.overnight_min_efficiency) if night else self.config.rolling_min_efficiency,
            "stop_atr": self.config.overnight_stop_atr if night else 0.20,
            "target_r": self.config.overnight_target_r if night else 1.5,
            "hold_minutes": self.config.overnight_max_hold_minutes if night else 30,
            "entry_start": self.session.open + timedelta(minutes=30),
            "entry_end": closing,
            "regime": "OVERNIGHT" if night else "RTH",
        }

    def quality_learner(self, timestamp):
        return self.overnight_learner if self.overnight(timestamp) else self.learner

    def flatten_time(self):
        timestamp = self.last_tick.timestamp if self.last_tick else self.session.open
        deadlines = [a-timedelta(minutes=1) for a,b in self.session.globex_breaks if b>timestamp]
        return min([self.session.close-timedelta(minutes=5)]+deadlines)

    def scheduled_exit_reason(self):
        return "MARKET_BREAK_EXIT" if self.flatten_time()<self.session.close-timedelta(minutes=5) else "TIME_EXIT"

    def _start_session(self, session):
        super()._start_session(session)
        self.cash_first = self.cash_last = None

    def process(self, tick):
        tick.validate()
        # Maintenance/weekend prints cannot fabricate fills or open new positions.
        local = tick.timestamp.astimezone(ET)
        if local.weekday() == 5 or local.weekday() == 6 and local.time() < time(18) or local.weekday() == 4 and local.time() >= time(17) or time(17) <= local.time() < time(18):
            if self.broker.position:
                self.log(tick, "exit", "UNRESOLVED_POSITION", detail="Market closed before a flatten execution")
                raise ValueError("UNRESOLVED_POSITION: supply a tradable pre-close tick path")
            return
        try:
            session = self.calendar.for_tick(tick)
        except ValueError:
            self.blocked = self.quality_fault = True
            self.daily_ranges.clear()
            self.previous_close = None
            self.log(tick, "eligibility", "UNRESOLVED_SESSION", futures_date=str(futures_day(tick.timestamp)))
            if self.broker.position:
                raise ValueError("UNRESOLVED_POSITION: position entered an unreviewed session")
            return
        if not session.market_open(tick.timestamp):
            if self.broker.position:
                raise ValueError("UNRESOLVED_POSITION: position reached a reviewed market break")
            return
        super().process(tick)
        if session.cash_open <= tick.timestamp < session.cash_close:
            self.cash_first = self.cash_first or tick
            self.cash_last = tick

    def gap_seconds(self, previous, current, session):
        seconds = (current - previous).total_seconds()
        for start, end in session.globex_breaks:
            seconds -= max(0, (min(current, end) - max(previous, start)).total_seconds())
        return seconds

    def _finish_session(self):
        if not self.session:
            return
        self._close_bar()
        s = self.session
        bars = [b for b in self.bars if s.cash_open <= b.start and b.end <= s.cash_close]
        expected = int((s.cash_close - s.cash_open).total_seconds() // 300)
        complete = (len(bars) == expected and self.cash_first is not None and self.cash_last is not None
                    and (self.cash_first.timestamp - s.cash_open).total_seconds() <= self.config.max_tick_gap_seconds
                    and (s.cash_close - self.cash_last.timestamp).total_seconds() <= self.config.max_tick_gap_seconds
                    and not self.quality_fault)
        if self.broker.position:
            self.log(s.close, "exit", "UNRESOLVED_POSITION")
            raise ValueError("UNRESOLVED_POSITION: full-session data ended before a tradable exit")
        if complete:
            high, low = max(b.high for b in bars), min(b.low for b in bars)
            tr = high - low
            if self.previous_close is not None and same_contract(self.previous_contract, s.contract):
                tr = max(tr, abs(high-self.previous_close), abs(low-self.previous_close))
            self.daily_ranges.append(tr)
            self.previous_close, self.previous_contract = bars[-1].close, s.contract
        else:
            self.daily_ranges.clear()
            self.previous_close = None
            self.log(s.cash_close, "eligibility", "DATA_GAP", bars=len(bars), expected=expected)
        self.session_records.append({"session": str(s.day), "complete": complete,
                                     "eligibility": s.eligibility, "atr20": self.atr,
                                     "bar_checksum": self.bar_digest.hexdigest(), "news_flags": s.news_flags})
        self.log(s.close, "session", "SESSION_COMPLETE" if complete else "SESSION_INCOMPLETE")
