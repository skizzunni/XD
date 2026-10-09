"""Append-only completed-bar capture from the read-only Topstep gateway."""

from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import time


BAR_KEYS = ("t", "o", "h", "l", "c", "v")


def validate_bar(bar):
    if not isinstance(bar, dict) or any(key not in bar for key in BAR_KEYS):
        raise ValueError("Topstep bar is missing required fields")
    timestamp = datetime.fromisoformat(str(bar["t"]).replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Topstep bar timestamp must be timezone-aware")
    prices = [bar[key] for key in ("o", "h", "l", "c")]
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) or value <= 0 for value in prices):
        raise ValueError("Topstep OHLC values must be finite and positive")
    if (bar["h"] < max(bar["o"], bar["l"], bar["c"])
            or bar["l"] > min(bar["o"], bar["h"], bar["c"])):
        raise ValueError("Topstep bar has inconsistent OHLC values")
    if (isinstance(bar["v"], bool) or not isinstance(bar["v"], (int, float))
            or bar["v"] < 0):
        raise ValueError("Topstep volume must be nonnegative")
    return timestamp


class CompletedBarCollector:
    def __init__(self, gateway, contract_id, output_path):
        self.gateway = gateway
        self.contract_id = contract_id
        self.output_path = Path(output_path)
        self.seen = set()
        if self.output_path.exists():
            for number, line in enumerate(self.output_path.read_text().splitlines(), 1):
                try:
                    bar = json.loads(line)
                    self.seen.add(validate_bar(bar).isoformat())
                except (ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(f"Invalid retained bar on line {number}") from exc

    def capture(self, now=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Capture time must include a timezone")
        bars = self.gateway.bars(self.contract_id, now-timedelta(hours=6), now,
                                 limit=500, unit=2, unit_number=1)
        accepted = []
        for bar in sorted(bars, key=lambda item: item.get("t", "")):
            timestamp = validate_bar(bar)
            key = timestamp.isoformat()
            if timestamp > now or key in self.seen:
                continue
            accepted.append({key: bar[key] for key in BAR_KEYS})
            self.seen.add(key)
        if accepted:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            with self.output_path.open("a", encoding="utf-8") as stream:
                for bar in accepted:
                    stream.write(json.dumps(bar, separators=(",", ":"), allow_nan=False)+"\n")
                stream.flush()
        return len(accepted)

    def run(self, poll_seconds=20):
        if poll_seconds < 10:
            raise ValueError("Poll at 10 seconds or slower to respect API limits")
        while True:
            self.capture()
            time.sleep(poll_seconds)
