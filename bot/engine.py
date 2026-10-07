from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
import hashlib
import json
import math

from .broker import PaperBroker
from .models import Bar, TICK, TICK_VALUE, round_outward
from .strategy import fvg_setup, momentum_direction


class Engine:
    def __init__(
        self, config, calendar, trade_start=None, trade_end=None, tick_index=None
    ):
        self.config, self.calendar = config, calendar
        self.trade_start, self.trade_end = trade_start, trade_end
        self.broker = PaperBroker(config)
        self.events, self.trades, self.session_records = [], [], []
        self.trade_audits = []
        self.session = None
        self.last_tick = None
        self.seen_ids = set()
        self.tick_index = tick_index
        self.daily_ranges = []
        self.previous_close = None
        self.previous_contract = None
        self.finalized = False
        self._reset_session()

    def _reset_session(self):
        self.bars, self.current_bar = [], None
        self.blocked = False
        self.direction = None
        self.m = None
        self.atr = None
        self.attempted = False
        self.setup = None
        self.setup_chosen = False
        self.first_tick = None
        self.session_last = None
        self.quality_fault = False
        self.bar_digest = hashlib.sha256()
        self.audit = None
        self.audit_last_tick = None

    def log(self, tick_or_time, stage, reason, **details):
        ts = (
            tick_or_time.timestamp
            if hasattr(tick_or_time, "timestamp")
            and not callable(tick_or_time.timestamp)
            else tick_or_time
        )
        self.events.append(
            {
                "event_id": len(self.events) + 1,
                "timestamp": ts.isoformat(),
                "session": str(self.session.day) if self.session else None,
                "strategy": self.config.strategy,
                "stage": stage,
                "reason": reason,
                **details,
            }
        )

    def fault(self, tick, reason):
        self.blocked = self.quality_fault = True
        self.log(tick, "eligibility", reason)
        if self.broker.position and tick.contract == self.broker.position.contract:
            self._exit(tick, "FAULT_EXIT")

    def _start_session(self, session):
        self.session = session
        self._reset_session()
        self.atr = (
            sum(self.daily_ranges[-20:]) / 20 if len(self.daily_ranges) >= 20 else None
        )
        reason = session.eligibility
        self.blocked = reason != "ELIGIBLE"
        self.log(
            session.open,
            "eligibility",
            reason,
            contract=session.contract,
            calendar_checksum=self.calendar_checksum
            if hasattr(self, "calendar_checksum")
            else None,
            atr20=self.atr,
            news_flags=session.news_flags,
        )
        if self.atr is None or self.atr <= 0:
            self.blocked = True
            self.log(
                session.open,
                "signal",
                "ATR_WARMUP",
                completed_sessions=len(self.daily_ranges),
            )
        if (
            self.trade_start
            and session.day < self.trade_start
            or self.trade_end
            and session.day > self.trade_end
        ):
            self.blocked = True

    def _close_bar(self):
        if self.current_bar is None:
            return
        bar = self.current_bar
        self.bars.append(bar)
        self.bar_digest.update(
            json.dumps(asdict(bar), default=str, sort_keys=True).encode()
        )
        self.current_bar = None
        if self.config.strategy == "C1" and not self.blocked and not self.setup_chosen:
            found = fvg_setup(self.bars, self.session, self.atr)
            if found:
                self.setup_chosen, self.setup = True, found
                self.log(
                    bar.end,
                    "signal",
                    "LONG" if found.direction > 0 else "SHORT",
                    midpoint=found.midpoint,
                    stop=found.stop,
                    expires=found.expires.isoformat(),
                    atr20=self.atr,
                    source_bars=[asdict(b) for b in self.bars[-3:]],
                )

    def _finish_session(self):
        if not self.session:
            return
        self._close_bar()
        s = self.session
        expected = int((s.close - s.open).total_seconds() // 300)
        complete = (
            len(self.bars) == expected
            and self.first_tick is not None
            and (self.first_tick.timestamp - s.open).total_seconds()
            <= self.config.max_tick_gap_seconds
            and (s.close - self.session_last.timestamp).total_seconds()
            <= self.config.max_tick_gap_seconds
            and not self.quality_fault
        )
        if not complete:
            self.log(
                s.close,
                "eligibility",
                "DATA_GAP",
                bars=len(self.bars),
                expected=expected,
            )
        if self.broker.position:
            # Never fabricate a fill from a stale last quote or silently carry an overnight position.
            self.log(
                s.close, "exit", "UNRESOLVED_POSITION", stop=self.broker.position.stop
            )
            raise ValueError(
                "UNRESOLVED_POSITION: data ended before a tradable flatten tick; supply the missing tick path"
            )
        if complete:
            high = max(b.high for b in self.bars)
            low = min(b.low for b in self.bars)
            tr = high - low
            if self.previous_close is not None and self.previous_contract == s.contract:
                tr = max(
                    tr, abs(high - self.previous_close), abs(low - self.previous_close)
                )
            self.daily_ranges.append(tr)
            self.previous_close, self.previous_contract = (
                self.bars[-1].close,
                s.contract,
            )
        else:
            # ATR20 is twenty consecutive completed full RTH sessions; a gap forces a fresh warmup.
            if not complete:
                self.daily_ranges.clear()
                self.previous_close = None
        self.session_records.append(
            {
                "session": str(s.day),
                "complete": complete,
                "eligibility": s.eligibility,
                "atr20": self.atr,
                "bar_checksum": self.bar_digest.hexdigest(),
                "news_flags": s.news_flags,
            }
        )
        self.log(
            s.close,
            "session",
            "SESSION_COMPLETE" if complete else "SESSION_INCOMPLETE",
            bar_checksum=self.bar_digest.hexdigest(),
        )

    def process(self, tick):
        if self.finalized:
            raise RuntimeError("Cannot append ticks after finish")
        try:
            tick.validate()
        except ValueError:
            self.blocked = self.quality_fault = True
            self.log(tick, "eligibility", "DATA_QUALITY")
            raise
        if self.last_tick and tick.timestamp < self.last_tick.timestamp:
            self.blocked = self.quality_fault = True
            self.log(tick, "eligibility", "BAD_TIMESTAMP")
            raise ValueError("BAD_TIMESTAMP: ticks must be chronological")
        key = (tick.contract, tick.tick_id)
        unique = (
            self.tick_index.register(key)
            if self.tick_index is not None
            else key not in self.seen_ids
        )
        if not unique:
            self.blocked = self.quality_fault = True
            self.log(tick, "eligibility", "DUPLICATE_TICK")
            raise ValueError("DUPLICATE_TICK: idempotent import key already seen")
        if self.tick_index is None:
            self.seen_ids.add(key)
        session = self.calendar.for_tick(tick)
        if self.session is None or self.session.day != session.day:
            prior = self.session
            self._finish_session()
            if prior:
                missing = [
                    d for d in self.calendar.sessions if prior.day < d < session.day
                ]
                if missing:
                    self.daily_ranges.clear()
                    self.previous_close = None
            self._start_session(session)
        self.last_tick = tick
        if not session.open <= tick.timestamp < session.close:
            if tick.timestamp >= session.close and self.broker.position:
                self.fault(tick, "DATA_GAP")
            return
        if tick.contract != session.contract:
            self.fault(tick, "UNRESOLVED_CONTRACT")
            raise ValueError(
                "UNRESOLVED_CONTRACT: tick differs from explicit session contract map"
            )
        if self.first_tick is None:
            self.first_tick = tick
            if (
                tick.timestamp - session.open
            ).total_seconds() > self.config.max_tick_gap_seconds:
                self.fault(tick, "DATA_GAP")
        previous = self.session_last
        if (
            previous
            and (tick.timestamp - previous.timestamp).total_seconds()
            > self.config.max_tick_gap_seconds
        ):
            self.fault(tick, "DATA_GAP")
        self.session_last = tick
        elapsed = int((tick.timestamp - session.open).total_seconds() // 300)
        start = session.open + timedelta(minutes=5 * elapsed)
        if self.current_bar and self.current_bar.start != start:
            self._close_bar()
        if self.current_bar is None:
            self.current_bar = Bar(
                start,
                start + timedelta(minutes=5),
                tick.price,
                tick.price,
                tick.price,
                tick.price,
                tick.volume,
            )
        else:
            self.current_bar.update(tick)
        if (
            self.config.strategy == "P0"
            and self.direction is None
            and tick.timestamp >= session.at(10, 0)
        ):
            opening = [b for b in self.bars if b.end <= session.at(10, 0)]
            if len(opening) != 6 or opening[0].start != session.open:
                self.fault(tick, "DATA_GAP")
                self.direction = 0
            elif self.atr:
                self.direction, self.m = momentum_direction(
                    opening[0].open,
                    opening[-1].close,
                    self.atr,
                    self.config.long_threshold,
                    self.config.short_threshold,
                )
                self.log(
                    session.at(10, 0),
                    "signal",
                    {1: "LONG", -1: "SHORT", 0: "BELOW_THRESHOLD"}[self.direction],
                    m=self.m,
                    atr20=self.atr,
                    source_bars=[asdict(b) for b in opening],
                )
        if self.broker.position:
            pos = self.broker.position
            self._mark_audit(tick)
            executable = (
                tick.bid
                if tick.bid is not None and pos.direction > 0
                else tick.ask
                if tick.ask is not None
                else tick.price
            )
            # Stop wins a same-tick collision; different ticks preserve observed order.
            if pos.direction * (executable - pos.stop) <= 0:
                self.blocked = True
                self._exit(tick, "EMERGENCY_STOP")
            elif (
                pos.target is not None
                and pos.direction * (executable - pos.target) >= 0
            ):
                self._exit(tick, "TARGET_EXIT")
            elif tick.timestamp >= session.at(
                15, 59 if self.config.strategy == "P0" else 55
            ):
                self._exit(tick, "TIME_EXIT")
            elif (
                self.config.daily_profit_target_usd
                and pos.direction
                * (self.broker.quote(tick, -pos.direction)[0] - pos.entry_fill)
                * 2
                - self.config.round_turn_fees_usd
                >= self.config.daily_profit_target_usd
            ):
                self.blocked = True
                self._exit(tick, "DAILY_PROFIT_TARGET")
            elif (
                pos.direction
                * (self.broker.quote(tick, -pos.direction)[0] - pos.entry_fill)
                * 2
                - self.config.round_turn_fees_usd
                <= -self.config.session_loss_budget_usd
            ):
                self.blocked = True
                self._exit(tick, "LOSS_BUDGET_EXIT")
            elif (
                self.config.break_even_trigger_r
                and not pos.break_even_armed
                and pos.direction * (executable - pos.entry_fill)
                >= self.config.break_even_trigger_r * pos.initial_risk
            ):
                # Estimated exit slippage + full fees; spread is already in entry fill / executable quote.
                cover = (
                    math.ceil(
                        self.config.round_turn_fees_usd / TICK_VALUE
                        + self.config.slippage_ticks_per_side
                    )
                    * TICK
                )
                stop = round_outward(
                    pos.entry_fill + pos.direction * cover, -pos.direction
                )
                if pos.direction * (executable - stop) > TICK:
                    pos.stop = stop
                    pos.break_even_armed = True
                    self.log(
                        tick, "order", "BREAK_EVEN_STOP", stop=stop, guaranteed=False
                    )
            return
        if self.blocked or self.attempted:
            return
        if self.config.strategy == "P0":
            if self.direction and session.at(15, 30) <= tick.timestamp < session.at(
                15, 59
            ):
                self._enter(tick, self.direction, previous)
        elif self.setup:
            if tick.timestamp >= self.setup.expires:
                self.attempted = True
                self.log(tick, "order", "MISSED", detail="SETUP_EXPIRED")
            elif tick.timestamp >= self.setup.formed and previous is not None:
                # Strict crossing: touching the midpoint is never a fill; no favorable OHLC assumptions.
                crossed = (
                    previous.price >= self.setup.midpoint > tick.price
                    if self.setup.direction > 0
                    else previous.price <= self.setup.midpoint < tick.price
                )
                if crossed:
                    self._enter(tick, self.setup.direction, previous, self.setup.stop)

    def _enter(self, tick, direction, previous, c1_stop=None):
        self.attempted = True
        fill, reference = self.broker.quote(tick, direction)
        trigger = previous.price if previous else reference
        if c1_stop is not None:
            trigger = self.setup.midpoint
        adverse = max(0, direction * (fill - trigger) / TICK)
        if adverse > self.config.slippage_cap_ticks:
            self.log(
                tick, "order", "MISSED", detail="SLIPPAGE_CAP", adverse_ticks=adverse
            )
            return
        stop = (
            c1_stop
            if c1_stop is not None
            else round_outward(fill - direction * 0.20 * self.atr, direction)
        )
        risk_ticks = direction * (fill - stop) / TICK
        actual_cost_floor = math.ceil(
            self.config.round_turn_fees_usd / TICK_VALUE
            + (
                (tick.ask - tick.bid) / TICK
                if tick.bid is not None
                else self.config.spread_ticks
            )
            + 2 * self.config.slippage_ticks_per_side
        )
        if (
            risk_ticks <= 0
            or c1_stop is not None
            and risk_ticks < 2 * actual_cost_floor
        ):
            self.blocked = True
            self.log(tick, "order", "REJECTED", detail="INITIAL_RISK_BELOW_COST_FLOOR")
            return
        if (
            risk_ticks * TICK_VALUE + self.config.round_turn_fees_usd
            > self.config.session_loss_budget_usd
        ):
            self.blocked = True
            self.log(
                tick,
                "order",
                "REJECTED",
                detail="STOP_EXCEEDS_LOSS_BUDGET",
                risk_ticks=risk_ticks,
            )
            return
        if (
            self.config.break_even_trigger_r
            and self.config.break_even_trigger_r * risk_ticks <= actual_cost_floor
        ):
            self.blocked = True
            self.log(tick, "order", "REJECTED", detail="BREAK_EVEN_TRIGGER_BELOW_COSTS")
            return
        target = None
        if c1_stop is not None:
            # Round target away from fill; avoid an optimistic sub-tick target.
            target = round_outward(
                fill + direction * 1.5 * risk_ticks * TICK, -direction
            )
        oid = f"{self.session.day}:{self.config.strategy}:entry"
        self.log(
            tick,
            "order",
            "SUBMITTED",
            order_id=oid,
            direction=direction,
            quantity=1,
            bid=tick.bid,
            ask=tick.ask,
        )
        self.broker.enter(tick, direction, stop, target)
        initial_net = (
            direction * (self.broker.quote(tick, -direction)[0] - fill) / TICK
            - self.config.round_turn_fees_usd / TICK_VALUE
        )
        self.audit = {
            "atr20": self.atr,
            "m": self.m,
            "signal_direction": direction,
            "initial_stop": stop,
            "entry_adverse_ticks": adverse,
            "pre_entry_bars": [asdict(b) for b in self.bars[-12:]],
            "opening_bars": [
                asdict(b) for b in self.bars if b.end <= self.session.at(10, 0)
            ],
            "max_net_ticks": initial_net,
            "min_net_ticks": initial_net,
            "observed_ticks_in_trade": 1,
            "quoted_entry": tick.bid is not None,
            "calendar_reason": self.session.eligibility,
            "stop_implementation": "local paper engine; no broker server",
            "fill_implementation": "deterministic tick simulation",
            "position_quantity": 1,
        }
        self.audit_last_tick = (tick.contract, tick.tick_id)
        self.log(
            tick,
            "order",
            "FILLED",
            order_id=oid,
            execution_id=f"{oid}:fill",
            price=fill,
            quantity=1,
            stop=stop,
            target=target,
        )
        self.log(
            tick, "order", "STOP_ACTIVE", stop=stop, implementation="local paper engine"
        )

    def _exit(self, tick, reason):
        self._mark_audit(tick)
        if self.audit:
            self.audit["final_stop"] = self.broker.position.stop
            self.audit["break_even_armed"] = self.broker.position.break_even_armed
            self.audit["quoted_exit"] = tick.bid is not None
        self.log(
            tick,
            "order",
            "SUBMITTED",
            order_id=f"{self.session.day}:{self.config.strategy}:exit",
            quantity=1,
            detail=reason,
            bid=tick.bid,
            ask=tick.ask,
        )
        trade = self.broker.exit(tick, self.session, reason)
        self.trades.append(trade)
        self.trade_audits.append(self.audit or {})
        self.log(tick, "exit", reason, **asdict(trade))
        if trade.net_ticks < 0:
            self.log(
                tick,
                "review",
                "LOSS_RECORDED",
                trade_number=len(self.trades),
                net_ticks=trade.net_ticks,
            )
        self.audit = None

    def _mark_audit(self, tick):
        if self.audit and self.broker.position:
            key = (tick.contract, tick.tick_id)
            if key == self.audit_last_tick:
                return
            self.audit_last_tick = key
            p = self.broker.position
            net = (
                p.direction
                * (self.broker.quote(tick, -p.direction)[0] - p.entry_fill)
                / TICK
                - self.config.round_turn_fees_usd / TICK_VALUE
            )
            self.audit["max_net_ticks"] = max(self.audit["max_net_ticks"], net)
            self.audit["min_net_ticks"] = min(self.audit["min_net_ticks"], net)
            self.audit["observed_ticks_in_trade"] += 1

    def finish(self):
        if not self.finalized:
            self._finish_session()
            self.finalized = True

    def event_hash(self):
        return hashlib.sha256(
            json.dumps(
                self.events, default=str, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
