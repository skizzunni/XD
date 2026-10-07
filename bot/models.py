from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime
import json
import math
from pathlib import Path

TICK = 0.25
TICK_VALUE = 0.50


@dataclass(frozen=True)
class Config:
    mode: str = "paper"
    strategy: str = "P0"
    long_threshold: float = 0.10
    short_threshold: float = 0.10
    atr_sessions: int = 20
    stop_atr: float = 0.20
    round_turn_fees_usd: float = 1.0
    spread_ticks: int = 1
    slippage_ticks_per_side: int = 1
    slippage_cap_ticks: int = 4
    costs_calibrated: bool = False
    session_loss_budget_usd: float = 100.0
    research_loss_budget_usd: float = 500.0
    max_tick_gap_seconds: int = 90
    bootstrap_samples: int = 2000
    bootstrap_block_sessions: int = 5
    pbo_blocks: int = 8
    minimum_segment_trades: int = 150
    seed: int = 1729
    daily_profit_target_usd: float = 0.0
    break_even_trigger_r: float = 0.0
    rolling_momentum_threshold: float = 0.05
    rolling_min_efficiency: float = 0.40
    adaptive_quality: bool = True

    def __post_init__(self):
        if self.mode != "paper":
            raise ValueError(
                "Only mode='paper' is implemented; live orders are unavailable"
            )
        if self.strategy not in {"P0", "C1", "R1"}:
            raise ValueError("strategy must be P0, C1 or R1")
        for f in fields(self):
            value = getattr(self, f.name)
            if f.type in {"int", "float", int, float}:
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    raise ValueError(f"{f.name} must be a finite number")
                if f.type in {"int", int} and not isinstance(value, int):
                    raise ValueError(f"{f.name} must be an integer")
                if value < 0:
                    raise ValueError(f"{f.name} must be nonnegative")
        if not isinstance(self.costs_calibrated, bool):
            raise ValueError("costs_calibrated must be a boolean")
        if not isinstance(self.adaptive_quality, bool):
            raise ValueError("adaptive_quality must be a boolean")
        if (
            not 0 < self.rolling_min_efficiency <= 0.85
            or self.rolling_momentum_threshold <= 0
        ):
            raise ValueError(
                "R1 requires efficiency in (0, .85] and a positive momentum threshold"
            )
        if self.atr_sessions != 20 or self.stop_atr != 0.20:
            raise ValueError(
                "P0/C1 baseline fixes ATR20 and the 0.20 ATR emergency stop"
            )
        if self.minimum_segment_trades < 150:
            raise ValueError(
                "Validation/holdout minimum cannot be weakened below 150 trades"
            )
        if self.max_tick_gap_seconds <= 0 or self.max_tick_gap_seconds > 300:
            raise ValueError("max_tick_gap_seconds must be in (0, 300]")
        if (
            min(
                self.long_threshold,
                self.short_threshold,
                self.session_loss_budget_usd,
                self.research_loss_budget_usd,
                self.bootstrap_block_sessions,
            )
            <= 0
        ):
            raise ValueError("Thresholds, budgets and block length must be positive")
        if self.bootstrap_samples < 100 or self.pbo_blocks < 4 or self.pbo_blocks % 2:
            raise ValueError(
                "Use at least 100 bootstrap samples and an even PBO block count >=4"
            )

    @property
    def all_in_ticks(self):
        return math.ceil(
            self.round_turn_fees_usd / TICK_VALUE
            + self.spread_ticks
            + 2 * self.slippage_ticks_per_side
        )

    @classmethod
    def load(cls, path):
        raw = json.loads(Path(path).read_text())
        return cls(**raw)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Tick:
    timestamp: datetime
    price: float
    volume: int
    contract: str
    tick_id: str
    bid: float | None = None
    ask: float | None = None

    def validate(self):
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("BAD_TIMESTAMP: timezone-aware timestamps required")
        if not self.tick_id or not self.contract:
            raise ValueError("DATA_QUALITY: contract and unique tick_id required")
        if (
            isinstance(self.volume, bool)
            or not isinstance(self.volume, int)
            or self.volume <= 0
        ):
            raise ValueError("ZERO_VOLUME: positive integer trade volume required")
        for price in (self.price, self.bid, self.ask):
            if price is not None and (
                not math.isfinite(price)
                or price <= 0
                or not math.isclose(price / TICK, round(price / TICK), abs_tol=1e-7)
            ):
                raise ValueError(
                    "BAD_PRICE: finite positive tick-aligned prices required"
                )
        if (self.bid is None) != (self.ask is None) or (
            self.bid is not None and self.bid > self.ask
        ):
            raise ValueError("BAD_QUOTE: require both bid/ask with bid <= ask")


@dataclass
class Bar:
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    ticks: int = 1

    def update(self, tick):
        self.high = max(self.high, tick.price)
        self.low = min(self.low, tick.price)
        self.close = tick.price
        self.volume += tick.volume
        self.ticks += 1


@dataclass
class Position:
    direction: int
    entry_time: str
    entry_fill: float
    entry_reference: float
    stop: float
    contract: str
    target: float | None = None
    initial_risk: float = 0.0
    break_even_armed: bool = False


@dataclass(frozen=True)
class Trade:
    session: str
    strategy: str
    direction: int
    contract: str
    entry_time: str
    exit_time: str
    entry_fill: float
    exit_fill: float
    gross_ticks: float
    net_ticks: float
    fees_usd: float
    execution_ticks: float
    all_in_ticks: int
    exit_reason: str
    news_flags: tuple[str, ...]


def round_outward(price, direction):
    return (
        math.floor(price / TICK + 1e-9)
        if direction > 0
        else math.ceil(price / TICK - 1e-9)
    ) * TICK
