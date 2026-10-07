"""Databento market data only. No order-routing API is used."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

from .contracts import contract_key
from .data import ET
from .models import Tick

DATASET = "GLBX.MDP3"
PRICE_SCALE = 1_000_000_000
UNDEFINED_PRICE = 2**63 - 1


class FeedDataError(ValueError):
    """Malformed or mismatched live data; requires operator inspection."""


def raw_symbol(contract):
    canonical = contract_key(contract)
    if not canonical.startswith("MNQ "):
        raise ValueError("Select an explicit quarterly MNQ contract, e.g. MNQ 12-26")
    month, year = canonical[4:].split("-")
    codes = {"03": "H", "06": "M", "09": "U", "12": "Z"}
    if month not in codes:
        raise ValueError("MNQ must use a quarterly expiry")
    return "MNQ" + codes[month] + str(int(year) % 10)


def timestamp_ns(value):
    value = int(value)
    seconds, nanos = divmod(value, PRICE_SCALE)
    return (datetime.fromtimestamp(seconds, timezone.utc)
            + timedelta(microseconds=nanos // 1000)).astimezone(ET)


def price(value):
    if value is None or int(value) == UNDEFINED_PRICE:
        raise ValueError("Databento supplied an undefined price")
    result = int(value) / PRICE_SCALE
    if not math.isfinite(result) or result <= 0:
        raise ValueError("Databento supplied an invalid price")
    return result


class TradeDecoder:
    """Require the requested raw-symbol mapping before accepting TBBO trades."""

    def __init__(self, contract):
        self.contract = contract_key(contract)
        self.symbol = raw_symbol(contract)
        self.instrument_ids = set()

    def decode(self, record):
        name = type(record).__name__
        if name == "ErrorMsg":
            raise ValueError("Databento feed error: " + str(record.err))
        if name == "SymbolMappingMsg":
            if str(record.stype_in_symbol) != self.symbol:
                raise FeedDataError("Databento mapped an unexpected contract")
            self.instrument_ids.add(int(record.instrument_id))
            return None
        if name != "MBP1Msg":
            return None
        action = getattr(record.action, "value", record.action)
        if action not in ("T", b"T", ord("T")):
            return None
        if int(record.instrument_id) not in self.instrument_ids:
            raise FeedDataError("Trade arrived without its verified contract mapping")
        level = record.levels[0]
        # DBN bytes include event/receive times, sequence, prices and sizes. Do
        # not treat a packet sequence alone as a unique trade identifier.
        digest = hashlib.sha256(bytes(record)).hexdigest()
        try:
            tick = Tick(timestamp_ns(record.ts_event), price(record.price),
                        int(record.size), self.contract, "db-" + digest,
                        price(level.bid_px), price(level.ask_px))
            tick.validate()
        except (ValueError, OverflowError) as exc:
            raise FeedDataError(str(exc)) from exc
        return tick


@dataclass(frozen=True)
class MinuteBar:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int


def validate_cash_bars(session, bars):
    expected = int((session.cash_close - session.cash_open).total_seconds() // 60)
    if len(bars) != expected:
        raise ValueError(f"History {session.day}: expected {expected} cash minutes, received {len(bars)}")
    for index, bar in enumerate(bars):
        timestamp = datetime.fromisoformat(bar.timestamp)
        if timestamp.tzinfo is None or timestamp != session.cash_open + timedelta(minutes=index):
            raise ValueError(f"History {session.day}: missing, repeated or misplaced minute")
        if (type(bar.volume) is not int or bar.volume <= 0
                or any(not math.isfinite(v) or v <= 0 or not math.isclose(v * 4, round(v * 4), abs_tol=1e-7)
                       for v in (bar.open, bar.high, bar.low, bar.close))
                or not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high):
            raise ValueError(f"History {session.day}: invalid OHLC/volume")
    return {"day": str(session.day), "high": max(b.high for b in bars),
            "low": min(b.low for b in bars), "close": bars[-1].close,
            "minutes": expected}


def atr_seed(calendar, target_day, summaries):
    prior = [s for day, s in sorted(calendar.sessions.items()) if day < target_day]
    if len(prior) < 21 or len(summaries) != 21:
        raise ValueError("ATR20 requires 21 reviewed, completed prior cash sessions")
    expected = prior[-21:]
    if [s["day"] for s in summaries] != [str(s.day) for s in expected]:
        raise ValueError("ATR history does not match the 21 immediately preceding calendar sessions")
    if any(not math.isfinite(v) or v <= 0 for v in
           (summaries[0]["high"], summaries[0]["low"], summaries[0]["close"])) or not summaries[0]["low"] <= summaries[0]["close"] <= summaries[0]["high"]:
        raise ValueError("Invalid historical cash summary")
    ranges, previous = [], summaries[0]["close"]
    for summary in summaries[1:]:
        high, low, close = summary["high"], summary["low"], summary["close"]
        if (any(not math.isfinite(v) or v <= 0 for v in (high, low, close))
                or not low <= close <= high):
            raise ValueError("Invalid historical cash summary")
        ranges.append(max(high - low, abs(high - previous), abs(low - previous)))
        previous = close
    if sum(ranges) <= 0:
        raise ValueError("ATR20 must be positive")
    return {"target_day": str(target_day), "ranges": ranges,
            "previous_close": previous, "previous_contract": expected[-1].contract,
            "cash_days": [s["day"] for s in summaries], "source": "Databento verified cash-minute bars"}


class HistoryLoader:
    """Cache real minute bars and enforce an explicit historical-download cap."""

    def __init__(self, client, calendar, contract, cache, max_cost=1.0, stop=None):
        if not math.isfinite(max_cost) or max_cost < 0:
            raise ValueError("Historical download budget must be finite and nonnegative")
        self.client, self.calendar = client, calendar
        self.contract, self.symbol = contract_key(contract), raw_symbol(contract)
        self.cache = Path(cache)
        self.max_cost, self.estimated_cost = max_cost, 0.0
        self.stop = stop

    def seed(self, target_day, now):
        prior = [s for day, s in sorted(self.calendar.sessions.items()) if day < target_day][-21:]
        if len(prior) != 21 or any(s.cash_close > now for s in prior):
            raise ValueError("Twenty-one prior cash sessions are not yet complete/reviewed")
        summaries = []
        for session in prior:
            if self.stop and self.stop.is_set():
                raise InterruptedError("Historical initialization cancelled")
            if not contract_key(session.contract) == self.contract:
                raise ValueError("History calendar changes contract; refresh the reviewed contract calendar")
            path = self.cache / f"{self.symbol}-{session.day}.json"
            if path.is_file():
                envelope = json.loads(path.read_text())
                encoded = json.dumps(envelope["bars"], sort_keys=True, separators=(",", ":")).encode()
                if (envelope.get("symbol") != self.symbol or envelope.get("dataset") != DATASET
                        or hashlib.sha256(encoded).hexdigest() != envelope.get("sha256")):
                    raise ValueError("Historical cache integrity check failed: " + str(path))
                bars = [MinuteBar(**row) for row in envelope["bars"]]
            else:
                params = {"dataset": DATASET, "schema": "ohlcv-1m", "symbols": [self.symbol],
                          "stype_in": "raw_symbol", "start": session.cash_open, "end": session.cash_close}
                cost = float(self.client.metadata.get_cost(**params))
                if not math.isfinite(cost) or cost < 0 or self.estimated_cost + cost > self.max_cost:
                    raise ValueError(f"Historical download estimate exceeds the ${self.max_cost:.2f} startup budget; no request was made for {session.day}")
                if self.stop and self.stop.is_set():
                    raise InterruptedError("Historical initialization cancelled")
                store = self.client.timeseries.get_range(**params)
                self.estimated_cost += cost
                bars = [MinuteBar(timestamp_ns(r.ts_event).isoformat(), price(r.open),
                                  price(r.high), price(r.low), price(r.close), int(r.volume))
                        for r in store if type(r).__name__ == "OHLCVMsg"]
                bars.sort(key=lambda b: b.timestamp)
                validate_cash_bars(session, bars)
                raw = [asdict(b) for b in bars]
                encoded = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
                self.cache.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps({"dataset": DATASET, "symbol": self.symbol,
                                                 "sha256": hashlib.sha256(encoded).hexdigest(), "bars": raw}))
                temporary.replace(path)
            summaries.append(validate_cash_bars(session, bars))
        return atr_seed(self.calendar, target_day, summaries)
