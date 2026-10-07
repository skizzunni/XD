"""Clearly synthetic integration fixtures, never presented as market evidence."""

import csv
from datetime import date, datetime, time, timedelta
import math
from pathlib import Path

from .data import ET, TICK_COLUMNS
from .report import write_json


def generate(directory, sessions=32):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    days, day = [], date(2026, 1, 5)
    while len(days) < sessions:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    calendar = {
        "version": 1,
        "source": "SYNTHETIC TEST FIXTURE, not an exchange calendar",
        "synthetic": True,
        "news_as_of": "2026-01-01T00:00:00-05:00",
        "sessions": [],
    }
    with (directory / "ticks.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TICK_COLUMNS)
        writer.writeheader()
        for n, day in enumerate(days):
            opening = datetime.combine(day, time(9, 30), ET)
            calendar["sessions"].append(
                {
                    "date": str(day),
                    "open": opening.isoformat(),
                    "close": (opening + timedelta(minutes=390)).isoformat(),
                    "contract": "MNQ-TEST",
                    "roll_day": False,
                    "news": {"flags": [], "releases": []},
                }
            )
            direction = 1 if n % 2 == 0 else -1
            for minute in range(390):
                # Full tick path with open momentum, midday volatility and mixed closing outcomes.
                if minute < 30:
                    offset = direction * minute * 0.5
                elif minute < 360:
                    offset = direction * 15 + 25 * math.sin(
                        (minute - 30) * math.pi / 330
                    )
                else:
                    outcome = -1 if n % 5 == 0 else 1
                    offset = direction * (15 + outcome * (minute - 360) * 0.75)
                price = round((20000 + n * 2 + offset) / 0.25) * 0.25
                writer.writerow(
                    {
                        "timestamp": (opening + timedelta(minutes=minute)).isoformat(),
                        "price": price,
                        "volume": 1,
                        "contract": "MNQ-TEST",
                        "tick_id": f"synthetic-{n}-{minute}",
                        "bid": "",
                        "ask": "",
                    }
                )
    write_json(directory / "calendar.json", calendar)
    return directory / "ticks.csv", directory / "calendar.json"
