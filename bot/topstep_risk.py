"""Topstep-specific entry risk gates for MNQ automation.

The module does not place orders. Firm limits are outer boundaries; the local
limits are intentionally smaller and are evaluated before every entry.
"""

from dataclasses import dataclass
from datetime import datetime, time
import math
from zoneinfo import ZoneInfo


CT = ZoneInfo("America/Chicago")
MNQ_TICK_VALUE = 0.50

ACCOUNT_RULES = {
    50_000: {"mll": 2_000.0, "profit_target": 3_000.0, "max_minis": 5},
    100_000: {"mll": 3_000.0, "profit_target": 6_000.0, "max_minis": 10},
    150_000: {"mll": 4_500.0, "profit_target": 9_000.0, "max_minis": 15},
}
SIMULATED_STAGES = {"practice", "trading_combine", "express_funded"}


@dataclass(frozen=True)
class TopstepRiskConfig:
    stage: str = "practice"
    account_size: int = 100_000
    per_position_risk_usd: float = 75.0
    session_loss_lock_usd: float = 300.0
    session_profit_lock_usd: float = 1_000.0
    mll_safety_buffer_usd: float = 750.0
    starting_micros: int = 1
    maximum_micros: int = 20
    maximum_consecutive_losses: int = 3
    round_turn_fees_usd_per_micro: float = 1.22
    modeled_round_turn_slippage_ticks: int = 2
    maximum_quote_age_seconds: float = 2.0

    def __post_init__(self):
        if self.stage not in SIMULATED_STAGES | {"live_funded"}:
            raise ValueError("Unknown Topstep stage")
        if self.stage == "live_funded":
            raise ValueError("Topstep API order automation is unavailable for Live Funded")
        if self.account_size not in ACCOUNT_RULES:
            raise ValueError("Topstep account size must be 50000, 100000 or 150000")
        numeric = (
            self.per_position_risk_usd,
            self.session_loss_lock_usd,
            self.session_profit_lock_usd,
            self.mll_safety_buffer_usd,
            self.round_turn_fees_usd_per_micro,
            self.maximum_quote_age_seconds,
        )
        if any(not math.isfinite(value) or value <= 0 for value in numeric):
            raise ValueError("Topstep risk limits must be finite and positive")
        if not 1 <= self.starting_micros <= self.maximum_micros:
            raise ValueError("Starting size must fit the local maximum")
        if self.maximum_consecutive_losses < 1:
            raise ValueError("Consecutive-loss lock must be positive")
        if self.modeled_round_turn_slippage_ticks < 0:
            raise ValueError("Slippage ticks cannot be negative")

    @property
    def firm_max_micros(self):
        return ACCOUNT_RULES[self.account_size]["max_minis"] * 10

    @property
    def firm_profit_target(self):
        return ACCOUNT_RULES[self.account_size]["profit_target"]


@dataclass(frozen=True)
class TopstepRiskState:
    timestamp: datetime
    balance: float
    unrealized_pnl: float
    session_net_pnl: float
    mll_floor: float
    open_micros: int = 0
    working_entry_micros: int = 0
    consecutive_losses: int = 0
    quote_age_seconds: float = 0.0
    news_blocked: bool = False
    connection_ready: bool = True
    position_protected: bool = True

    def __post_init__(self):
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("Risk timestamps must include a timezone")
        for value in (self.balance, self.unrealized_pnl, self.session_net_pnl,
                      self.mll_floor, self.quote_age_seconds):
            if not math.isfinite(value):
                raise ValueError("Risk state values must be finite")
        if min(self.open_micros, self.working_entry_micros, self.consecutive_losses) < 0:
            raise ValueError("Position/order/loss counts cannot be negative")


@dataclass(frozen=True)
class EntryDecision:
    allowed: bool
    reason: str
    quantity: int
    per_micro_reservation_usd: float
    position_reservation_usd: float
    available_risk_usd: float
    mll_headroom_usd: float


def topstep_entry_hours(timestamp):
    """Topstep MNQ entry window, with a two-minute pre-flatten buffer."""
    local = timestamp.astimezone(CT)
    weekday, clock = local.weekday(), local.time()
    if weekday == 5 or weekday == 6 and clock < time(17):
        return False
    if weekday == 4 and clock >= time(15, 8):
        return False
    return clock < time(15, 8) or clock >= time(17)


def decide_entry(config, state, stop_ticks, suggested_micros, platform_max_micros):
    """Return the cash-fitted MNQ size or a fail-closed rejection."""
    if not math.isfinite(stop_ticks) or stop_ticks <= 0:
        raise ValueError("A positive finite structural stop is required")
    if type(suggested_micros) is not int or suggested_micros <= 0:
        raise ValueError("Suggested MNQ size must be a positive integer")
    if type(platform_max_micros) is not int or platform_max_micros <= 0:
        raise ValueError("Read the current platform contract limit before trading")
    headroom = state.balance + state.unrealized_pnl - state.mll_floor
    per_micro = (stop_ticks * MNQ_TICK_VALUE
                 + config.round_turn_fees_usd_per_micro
                 + config.modeled_round_turn_slippage_ticks * MNQ_TICK_VALUE)
    remaining_daily = config.session_loss_lock_usd + min(0.0, state.session_net_pnl)
    available = max(0.0, min(config.per_position_risk_usd, remaining_daily,
                             headroom - config.mll_safety_buffer_usd))
    base = EntryDecision(False, "UNKNOWN", 0, per_micro, 0.0, available, headroom)
    checks = (
        (not state.connection_ready, "CONNECTION_NOT_READY"),
        (state.quote_age_seconds > config.maximum_quote_age_seconds, "STALE_QUOTE"),
        (not topstep_entry_hours(state.timestamp), "OUTSIDE_TOPSTEP_ENTRY_HOURS"),
        (state.news_blocked, "HIGH_IMPACT_NEWS_BUFFER"),
        (state.open_micros > 0 or state.working_entry_micros > 0, "POSITION_OR_ENTRY_ALREADY_ACTIVE"),
        (not state.position_protected, "UNPROTECTED_POSITION_LATCH"),
        (state.session_net_pnl <= -config.session_loss_lock_usd, "SESSION_LOSS_LOCK"),
        (state.session_net_pnl >= config.session_profit_lock_usd, "SESSION_PROFIT_LOCK"),
        (state.consecutive_losses >= config.maximum_consecutive_losses, "LOSS_STREAK_LOCK"),
        (headroom <= config.mll_safety_buffer_usd, "MLL_SAFETY_BUFFER"),
    )
    for failed, reason in checks:
        if failed:
            return EntryDecision(**{**base.__dict__, "reason": reason})
    capacity = min(config.firm_max_micros, platform_max_micros,
                   config.maximum_micros)
    quantity = min(suggested_micros, capacity, math.floor(available / per_micro))
    if quantity < 1:
        return EntryDecision(**{**base.__dict__, "reason": "RISK_CANNOT_FIT_ONE_MNQ"})
    reservation = quantity * per_micro
    return EntryDecision(True, "ENTRY_ALLOWED", quantity, per_micro,
                         reservation, available, headroom)
