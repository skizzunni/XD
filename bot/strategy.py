"""Pure closed-bar rules; the engine supplies only already-completed bars."""

from dataclasses import dataclass
from datetime import timedelta

from .models import TICK, round_outward


def momentum_direction(
    open_price, close_price, atr, long_threshold=0.1, short_threshold=0.1
):
    if atr <= 0:
        raise ValueError("ATR must be positive")
    m = (close_price - open_price) / atr
    direction = 1 if m >= long_threshold else -1 if m <= -short_threshold else 0
    return direction, m


@dataclass(frozen=True)
class FVG:
    direction: int
    midpoint: float
    stop: float
    formed: object
    expires: object
    width: float


@dataclass(frozen=True)
class RollingSetup:
    direction: int
    reference: float
    stop: float
    formed: object
    expires: object
    efficiency: float
    momentum: float


def rolling_setup(bars, session, atr, momentum_threshold=0.05, min_efficiency=0.40,
                  entry_start=None, entry_end=None, max_stop_atr=0.20):
    if len(bars) < 6 or atr <= 0:
        return None
    window = bars[-6:]
    if any(a.end != b.start for a, b in zip(window, window[1:])):
        return None
    a, b, c = window[-3:]
    if not (entry_start or session.at(10, 0)) <= c.end <= (entry_end or session.at(15, 45)):
        return None
    change = c.close - window[0].open
    momentum = change / atr
    direction = (
        1
        if momentum >= momentum_threshold
        else -1
        if momentum <= -momentum_threshold
        else 0
    )
    path = abs(window[0].close - window[0].open) + sum(
        abs(y.close - x.close) for x, y in zip(window, window[1:])
    )
    efficiency = abs(change) / path if path else 0
    width = c.high - c.low
    if (
        direction == 0
        or efficiency < min_efficiency
        or width <= 0
        or width > 0.25 * atr
    ):
        return None
    if direction * (b.close - b.open) >= 0:
        return None  # A closed countertrend pullback precedes the breakout.
    if direction > 0:
        if c.close <= max(a.high, b.high) or (c.close - c.low) / width < 0.70:
            return None
        stop = b.low - TICK
    else:
        if c.close >= min(a.low, b.low) or (c.high - c.close) / width < 0.70:
            return None
        stop = b.high + TICK
    risk = direction * (c.close - stop)
    if risk <= 0 or risk > max_stop_atr * atr:
        return None
    return RollingSetup(
        direction,
        c.close,
        round_outward(stop, direction),
        c.end,
        c.end + timedelta(minutes=5),
        efficiency,
        momentum,
    )


def fvg_setup(bars, session, atr):
    if len(bars) < 3:
        return None
    a, b, c = bars[-3:]
    if (
        a.end != b.start
        or b.end != c.start
        or not session.at(9, 30) <= c.end <= session.at(11, 30)
    ):
        return None
    if a.high < c.low:
        direction, low, high, stop = 1, a.high, c.low, a.low - TICK
    elif a.low > c.high:
        direction, low, high, stop = -1, c.high, a.low, a.high + TICK
    else:
        return None
    if high - low < max(2 * TICK, 0.01 * atr) or b.high - b.low < 0.05 * atr:
        return None
    if direction * (c.close - bars[0].open) <= 0:
        return None
    # Midpoints between ticks round into the zone; require strict trade-through of the original midpoint.
    return FVG(
        direction,
        (low + high) / 2,
        round_outward(stop, direction),
        c.end,
        c.end + timedelta(minutes=30),
        high - low,
    )
