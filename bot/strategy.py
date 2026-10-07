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
