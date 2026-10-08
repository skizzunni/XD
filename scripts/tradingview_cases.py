"""Independent Python-engine oracle and synthetic bars for the Pine port checks."""

import json
import random
from datetime import timedelta
from pathlib import Path

from bot.data import Calendar
from bot.models import Bar
from bot.fullsession import FullSessionCalendar
from bot.sizing import adaptive_state, budget_quantity
from bot.strategy import rolling_setup, runner_stop

ROOT = Path(__file__).resolve().parents[1]


def cases():
    rng = random.Random(7731)
    size = []
    for case in range(100):
        start, maximum = rng.randint(1, 6), 10
        outcomes = []
        # Include profitable growth, losing streaks, faults and weak-window latching.
        for step in range(60):
            value = 0.8 if case < 10 else (-1 if case < 20 else rng.choice([-1.5, -1, -.4, 0, .5, 1, 1.5, 2.5]))
            eligible = True if case < 20 else rng.random() >= 0.08
            outcomes.append({"net_r": value, "eligible": eligible})
            expected = adaptive_state(outcomes, start, maximum)
            size.append({"reset": step == 0, "start": start, "maximum": maximum, "value": value, "eligible": eligible, "q": expected["suggested_contracts"], "count": expected["observations"], "reason": expected["reason"], "net": expected["recent_net_r"], "dd": expected["recent_drawdown_r"]})
    fit = []
    for _ in range(1500):
        q, risk = rng.randint(1, 10), rng.uniform(.1, 1000)
        remaining = rng.choice([q*risk, q*risk-1e-10, q*risk+1e-10, rng.uniform(-100, 1000)])
        fit.append({"q": q, "risk": risk, "remaining": remaining, "expected": budget_quantity(q, risk, remaining)})
    runner = []
    for _ in range(300):
        d = rng.choice([-1, 1])
        fill = rng.randint(29000, 31000) / 4
        risk = rng.randint(1, 120) / 4
        peak = fill + d * rng.randint(0, 300) / 4
        stop = fill + d * rng.randint(-120, 120) / 4
        quote = peak - d * rng.randint(0, 40) / 4
        runner.append({"d": d, "fill": fill, "risk": risk, "peak": peak, "stop": stop, "quote": quote, "expected": runner_stop(d, fill, risk, peak, stop, quote)})
    setup = []
    session = next(iter(FullSessionCalendar(Calendar(ROOT / "ninjatrader/calendars/mnq-dec26-full-session-2026-10-07-30/calendar.json")).sessions.values()))
    model = [(0, 3, -1, 2), (2, 5, 1, 4), (4, 7, 3, 6), (6, 9, 5, 8), (8, 9, 6.5, 7), (7, 13, 6.5, 12)]
    base = session.cash_open + timedelta(minutes=30)
    for case in range(300):
        direction = rng.choice([-1, 1])
        prices = model if case < 100 else [(0, 2, -2, rng.randint(-8, 8)/4) for _ in range(6)]
        bars = []
        for i, (o, high, low, c) in enumerate(prices):
            start = base + timedelta(minutes=5 * (case*7 + i))
            end = start + timedelta(minutes=5)
            if case % 11 == 0 and i == 3:
                start += timedelta(seconds=1)
            bars.append(Bar(start, end, 30000 + direction*o, 30000 + (high if direction > 0 else -low), 30000 + (low if direction > 0 else -high), 30000 + direction*c, 100))
        atr, minimum, max_stop = rng.choice([60, 100, 150, 200]), rng.choice([.4, .55, .7]), rng.choice([.1, .2])
        result = rolling_setup(bars, session, atr, min_efficiency=minimum, entry_start=bars[0].start-timedelta(days=1), entry_end=bars[-1].end+timedelta(days=1), max_stop_atr=max_stop)
        setup.append({"bars": [{"openTime": int(b.start.timestamp()*1000), "closeTime": int(b.end.timestamp()*1000), "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": case} for b in bars], "atr": atr, "minimum": minimum, "max_stop": max_stop, "direction": result.direction if result else 0, "stop": result.stop if result else None})
    allowance = []
    for _ in range(1000):
        budget, pnl = rng.uniform(1, 1000), rng.uniform(-1100, 1000)
        capped, cap = rng.choice([True, False]), rng.uniform(1, 1000)
        remaining = max(0, budget + min(0, pnl))
        allowance.append({"budget": budget, "pnl": pnl, "capped": capped, "cap": cap,
                          "expected": min(remaining, cap) if capped else remaining})
    return {"size": size, "fit": fit, "runner": runner, "setup": setup, "allowance": allowance}


if __name__ == "__main__":
    print(json.dumps(cases(), separators=(",", ":")))
