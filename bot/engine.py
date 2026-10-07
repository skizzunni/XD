from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
import hashlib
import json
import math

from .broker import PaperBroker
from .contracts import contract_key, same_contract
from .learning import QualityLearner
from .models import Bar, TICK, TICK_VALUE, round_outward
from .strategy import fvg_setup, momentum_direction, rolling_setup


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
        self.learner = QualityLearner(
            config.rolling_min_efficiency, config.adaptive_quality
        )
        self.last_exit_time = None
        self.trade_sequence = 0
        self._reset_session()

    @property
    def rolling(self):
        return self.config.strategy in {"R1", "R2"}

    def rolling_rules(self, timestamp):
        return {"efficiency": self.config.rolling_min_efficiency, "stop_atr": 0.20,
                "target_r": 1.5, "hold_minutes": 30,
                "entry_start": self.session.at(10, 0), "entry_end": self.session.at(15, 45),
                "regime": "RTH"}

    def quality_learner(self, timestamp):
        return self.learner

    def flatten_time(self):
        return self.session.close - timedelta(minutes=5) if self.config.strategy == "R2" else self.session.at(15, 59 if self.config.strategy == "P0" else 55)

    def gap_seconds(self, previous, current, session):
        return (current - previous).total_seconds()

    def scheduled_exit_reason(self):
        return "TIME_EXIT"

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
        self.day_realized = 0.0
        self.rolling_signal = None
        self.learning_at_entry = None
        self.active_order_id = None
        self.entry_rules = None

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
        if self.broker.position and same_contract(tick.contract, self.broker.position.contract):
            self._exit(tick, "FAULT_EXIT")

    def _start_session(self, session):
        self.session = session
        self._reset_session()
        self.atr = (
            sum(self.daily_ranges[-20:]) / 20 if len(self.daily_ranges) >= 20 else None
        )
        reason = self._eligibility(session)
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

    def _eligibility(self, session):
        if (
            self.rolling
            and session.trade_enabled
            and session.eligibility == "NEWS_WINDOW"
        ):
            return "ELIGIBLE"
        return session.eligibility

    def _close_bar(self):
        if self.current_bar is None:
            return
        bar = self.current_bar
        self.bars.append(bar)
        self.bar_digest.update(
            json.dumps(asdict(bar), default=str, sort_keys=True).encode()
        )
        self.current_bar = None
        if (
            self.rolling
            and not self.blocked
            and not self.broker.position
        ):
            self.rolling_signal = None
            self.attempted = False
            if self.last_exit_time is None or bar.end > self.last_exit_time:
                rules = self.rolling_rules(bar.end)
                found = rolling_setup(
                    self.bars,
                    self.session,
                    self.atr,
                    self.config.rolling_momentum_threshold,
                    rules["efficiency"],
                    entry_start=rules["entry_start"], entry_end=rules["entry_end"],
                    max_stop_atr=rules["stop_atr"],
                )
                if found:
                    state = self.quality_learner(found.formed).state(found.direction, found.formed)
                    if found.efficiency >= state["min_efficiency"]:
                        self.rolling_signal = found
                        self.entry_rules = rules
                        self.learning_at_entry = state
                        self.m = found.momentum
                        self.log(
                            bar.end,
                            "signal",
                            "LONG" if found.direction > 0 else "SHORT",
                            setup=self.config.strategy,
                            regime=rules["regime"],
                            efficiency=found.efficiency,
                            min_efficiency=state["min_efficiency"],
                            source_bars=[asdict(b) for b in self.bars[-6:]],
                        )
                    else:
                        self.log(
                            bar.end,
                            "signal",
                            "QUALITY_FILTER",
                            efficiency=found.efficiency,
                            **state,
                        )
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
            if self.previous_close is not None and same_contract(self.previous_contract, s.contract):
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
                "eligibility": self._eligibility(s),
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
        key = (contract_key(tick.contract), tick.tick_id)
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
        if not same_contract(tick.contract, session.contract):
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
            and self.gap_seconds(previous.timestamp, tick.timestamp, session)
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
                if not self.rolling:
                    self.blocked = True
                self._exit(tick, "EMERGENCY_STOP")
            elif (
                pos.target is not None
                and pos.direction * (executable - pos.target) >= 0
            ):
                self._exit(tick, "TARGET_EXIT")
            elif tick.timestamp >= self.flatten_time():
                self._exit(tick, self.scheduled_exit_reason())
            elif self.rolling and session.news_paused(tick.timestamp):
                self._exit(tick, "NEWS_EXIT")
            elif (
                self.rolling
                and tick.timestamp
                >= datetime.fromisoformat(pos.entry_time) + timedelta(minutes=(self.entry_rules or {}).get("hold_minutes", 30))
            ):
                self._exit(tick, "HOLD_EXIT")
            elif (
                self.config.daily_profit_target_usd
                and self.day_realized
                + pos.direction
                * (self.broker.quote(tick, -pos.direction)[0] - pos.entry_fill)
                * 2
                - self.config.round_turn_fees_usd
                >= self.config.daily_profit_target_usd
            ):
                self.blocked = True
                self._exit(tick, "DAILY_PROFIT_TARGET")
            elif (
                self.day_realized
                + pos.direction
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
        if self.rolling and session.news_paused(tick.timestamp):
            if self.rolling_signal:
                self.attempted = True
                self.log(tick, "risk", "NEWS_PAUSE")
            return
        if (
            self.day_realized <= -self.config.session_loss_budget_usd
            or self.config.daily_profit_target_usd
            and self.day_realized >= self.config.daily_profit_target_usd
        ):
            self.blocked = True
            self.log(tick, "risk", "DAILY_RISK_LIMIT")
            return
        if self.config.strategy == "P0":
            if self.direction and session.at(15, 30) <= tick.timestamp < session.at(
                15, 59
            ):
                self._enter(tick, self.direction, previous)
        elif self.rolling:
            candidate = self.rolling_signal
            if (
                candidate
                and candidate.formed <= tick.timestamp < candidate.expires
                and tick.timestamp < self.flatten_time()
            ):
                self._enter(tick, candidate.direction, previous, candidate.stop)
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
        if self.config.strategy == "R2" and (tick.bid is None or tick.ask is None
                or (tick.ask - tick.bid) / TICK > self.config.full_session_max_spread_ticks):
            self.log(tick, "order", "REJECTED", detail="R2_QUOTE_OR_SPREAD_FILTER")
            return
        fill, reference = self.broker.quote(tick, direction)
        trigger = previous.price if previous else reference
        if c1_stop is not None:
            trigger = (
                self.rolling_signal.reference
                if self.rolling
                else self.setup.midpoint
            )
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
            or self.rolling
            and risk_ticks * TICK > (self.entry_rules or {}).get("stop_atr", 0.20) * self.atr
            or c1_stop is not None
            and risk_ticks < 2 * actual_cost_floor
        ):
            self.blocked = not self.rolling
            self.log(tick, "order", "REJECTED", detail="INITIAL_RISK_BELOW_COST_FLOOR")
            return
        if (
            risk_ticks * TICK_VALUE
            + self.config.round_turn_fees_usd
            + self.config.slippage_ticks_per_side * TICK_VALUE
            > self.config.session_loss_budget_usd + min(0, self.day_realized)
        ):
            self.blocked = not self.rolling
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
            self.blocked = not self.rolling
            self.log(tick, "order", "REJECTED", detail="BREAK_EVEN_TRIGGER_BELOW_COSTS")
            return
        target = None
        if c1_stop is not None:
            # Round target away from fill; avoid an optimistic sub-tick target.
            target = round_outward(
                fill + direction * (self.entry_rules or {}).get("target_r", 1.5) * risk_ticks * TICK, -direction
            )
        self.trade_sequence += 1
        oid = f"{self.session.day}:{self.config.strategy}:entry:{self.trade_sequence}"
        self.active_order_id = oid
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
            "calendar_reason": self._eligibility(self.session),
            "stop_implementation": "local paper engine; no broker server",
            "fill_implementation": "deterministic tick simulation",
            "position_quantity": 1,
            "day_realized_before": self.day_realized,
            "order_id": oid,
            "learning_at_entry": self.learning_at_entry,
            "rolling_efficiency": self.rolling_signal.efficiency
            if self.rolling_signal
            else None,
            "setup_formed": self.rolling_signal.formed.isoformat()
            if self.rolling_signal
            else None,
        }
        self.audit_last_tick = (contract_key(tick.contract), tick.tick_id)
        if self.config.strategy == "R2":
            self.audit["full_session_rules"] = self.entry_rules
            self.audit["futures_day"] = str(self.session.day)
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
            order_id=f"{self.active_order_id}:exit",
            quantity=1,
            detail=reason,
            bid=tick.bid,
            ask=tick.ask,
        )
        trade = self.broker.exit(tick, self.session, reason)
        self.trades.append(trade)
        self.day_realized += trade.net_ticks * TICK_VALUE
        if self.audit:
            self.audit["day_realized_after"] = self.day_realized
        self.last_exit_time = tick.timestamp
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
        if (
            self.rolling
            and not self.quality_fault
            and reason != "FAULT_EXIT"
        ):
            learner = self.quality_learner(datetime.fromisoformat(trade.entry_time))
            learner.record(
                self.active_order_id,
                tick.timestamp,
                trade.direction,
                trade.net_ticks * TICK_VALUE,
            )
            self.log(
                tick,
                "learning",
                "ADAPTATION_UPDATE",
                regime=(self.entry_rules or {}).get("regime", "RTH"),
                **learner.state(
                    trade.direction, tick.timestamp + timedelta(microseconds=1)
                ),
            )
        elif self.rolling:
            self.log(
                tick,
                "learning",
                "QUALITY_SAMPLE_EXCLUDED",
                detail="Data/order faults count toward risk, but do not train the entry filter",
            )
        if self.rolling:
            self.rolling_signal = None
        if (
            self.day_realized <= -self.config.session_loss_budget_usd
            or self.config.daily_profit_target_usd
            and self.day_realized >= self.config.daily_profit_target_usd
            or reason
            in {"FAULT_EXIT", "TIME_EXIT", "LOSS_BUDGET_EXIT", "DAILY_PROFIT_TARGET"}
        ):
            self.blocked = True
        self.audit = None

    def _mark_audit(self, tick):
        if self.audit and self.broker.position:
            key = (contract_key(tick.contract), tick.tick_id)
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
