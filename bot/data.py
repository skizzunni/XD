from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import Tick

ET = ZoneInfo("America/New_York")
TRANSFORMATION_VERSION = "mnq-ticks-rth-v1"


def parse_time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("BAD_TIMESTAMP: timestamp must include UTC offset")
    return result.astimezone(ET)


def checksum(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class Session:
    day: date
    open: datetime
    close: datetime
    contract: str
    roll_day: bool
    news_flags: tuple[str, ...]
    releases: tuple[datetime, ...]
    trade_enabled: bool = True
    globex_open: datetime | None = None
    globex_close: datetime | None = None
    globex_breaks: tuple = ()

    @property
    def late_news(self):
        return any(
            time(15, 25) <= t.timetz().replace(tzinfo=None) <= time(16)
            for t in self.releases
        )

    @property
    def news_windows(self):
        windows = []
        for release in self.releases:
            start, end = (
                max(self.open, release - timedelta(minutes=5)),
                min(self.close, release + timedelta(minutes=10)),
            )
            if start < end:
                windows.append((start, end))
        return tuple(sorted(windows))

    def news_paused(self, timestamp):
        return any(start <= timestamp < end for start, end in self.news_windows)

    @property
    def globex_news_windows(self):
        if self.globex_open is None:
            return ()
        return tuple(sorted((max(self.globex_open, r-timedelta(minutes=5)), min(self.globex_close, r+timedelta(minutes=10)))
                            for r in self.releases if self.globex_open <= r < self.globex_close))

    @property
    def eligibility(self):
        if self.close.timetz().replace(tzinfo=None) != time(16):
            return "EARLY_CLOSE"
        if self.roll_day:
            return "ROLL_DAY"
        if not self.contract:
            return "UNRESOLVED_CONTRACT"
        if self.late_news:
            return "NEWS_WINDOW"
        if not self.trade_enabled:
            return "WARMUP_ONLY"
        return "ELIGIBLE"

    def at(self, hour, minute):
        return datetime.combine(self.day, time(hour, minute), ET)


class Calendar:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        raw = json.loads(Path(path).read_text())
        if (
            raw.get("version") != 1
            or not raw.get("source")
            or not raw.get("news_as_of")
        ):
            raise ValueError(
                "Calendar requires version=1, source and frozen news_as_of metadata"
            )
        parse_time(raw["news_as_of"])
        self.synthetic = raw.get("synthetic") is True
        self.sessions = {}
        for row in raw["sessions"]:
            day = date.fromisoformat(row["date"])
            if day in self.sessions or day.weekday() > 4:
                raise ValueError("Duplicate/weekend calendar date")
            start, end = parse_time(row["open"]), parse_time(row["close"])
            if (
                start.date() != day
                or end.date() != day
                or start.time() != time(9, 30)
                or end <= start
                or end.time() > time(16)
                or (end - start).total_seconds() % 300
            ):
                raise ValueError("Invalid RTH session boundaries")
            if not isinstance(row["roll_day"], bool) or not isinstance(
                row["contract"], str
            ):
                raise ValueError(
                    "Explicit contract mapping and boolean roll_day required"
                )
            if not isinstance(row.get("trade_enabled", True), bool):
                raise ValueError("trade_enabled must be a boolean")
            news = row["news"]
            if not isinstance(news["flags"], list) or not isinstance(
                news["releases"], list
            ):
                raise ValueError(
                    "Explicit macro flags and release list required for every session"
                )
            releases = []
            for release in news["releases"]:
                if not release.get("name") or "revision" not in release:
                    raise ValueError("News release requires name and revision history")
                ts = parse_time(release["timestamp"])
                if ts.date() != day and not (row.get("globex") and parse_time(row["globex"]["open"]) <= ts < parse_time(row["globex"]["close"])):
                    raise ValueError("News date differs from session")
                releases.append(ts)
            extended = row.get("globex")
            extended_open = extended_close = None
            extended_breaks = ()
            if extended is not None:
                extended_open, extended_close = parse_time(extended["open"]), parse_time(extended["close"])
                if (extended_open >= start or extended_close < end
                        or extended_close.date() != day
                        or extended_open.time() != time(18)
                        or extended_open.date() != day - timedelta(days=1)
                        or extended_close.time() > time(17)):
                    raise ValueError("Invalid reviewed Globex session boundaries")
                extended_breaks = tuple((parse_time(a), parse_time(b)) for a, b in extended.get("breaks", []))
                if any(not extended_open < a < b < extended_close for a, b in extended_breaks):
                    raise ValueError("Invalid reviewed Globex break")
            self.sessions[day] = Session(
                day,
                start,
                end,
                row["contract"],
                row["roll_day"],
                tuple(news["flags"]),
                tuple(releases),
                row.get("trade_enabled", True),
                extended_open, extended_close, extended_breaks,
            )
        if not self.sessions:
            raise ValueError("Calendar contains no sessions")

    def for_tick(self, tick):
        day = tick.timestamp.astimezone(ET).date()
        if day not in self.sessions:
            raise ValueError(
                f"UNRESOLVED_SESSION: {day} is absent from the frozen calendar"
            )
        return self.sessions[day]


TICK_COLUMNS = ("timestamp", "price", "volume", "contract", "tick_id", "bid", "ask")


def tick_from_row(row):
    tick = Tick(
        parse_time(row["timestamp"]),
        float(row["price"]),
        int(row["volume"]),
        row["contract"],
        row["tick_id"],
        float(row["bid"]) if row.get("bid") else None,
        float(row["ask"]) if row.get("ask") else None,
    )
    tick.validate()
    return tick


def read_ticks(path):
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if not set(TICK_COLUMNS[:5]).issubset(reader.fieldnames or ()):
            raise ValueError(
                "Tick CSV requires timestamp,price,volume,contract,tick_id; OHLC bars cannot model tick fills"
            )
        for number, row in enumerate(reader, 2):
            try:
                yield tick_from_row(row)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid tick at CSV line {number}: {exc}") from exc


def code_hash():
    root = Path(__file__).resolve().parent.parent
    h = hashlib.sha256()
    for path in sorted([*root.glob("bot/*.py"), root / "main.py"]):
        h.update(str(path.relative_to(root)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()
