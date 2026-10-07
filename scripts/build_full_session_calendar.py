"""Build the October 2026 full-session paper calendar from retained official data.

CME dated hours must be fetched from the documented public endpoint first.
Preserves the old RTH/playback calendars; does not enable NinjaTrader.
"""

import argparse
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import gzip
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bot.data import Calendar, ET


class Node:
    def __init__(self, tag="root", attrs=(), parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []

    def text(self):
        return " ".join(c.text() if isinstance(c, Node) else c for c in self.children)

    def find(self, tag=None, **attrs):
        for child in self.children:
            if isinstance(child, Node):
                if (tag is None or child.tag == tag) and all(child.attrs.get(k) == v for k, v in attrs.items()):
                    yield child
                yield from child.find(tag, **attrs)


class Tree(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in {"br", "img", "input", "meta", "link", "hr", "source", "wbr", "area"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def release_clock(value):
    found = re.search(r"(\d{1,2}):(\d{2})\s*([ap])\.?\s*m\.?", value, re.I)
    if not found:
        return None
    hour, minute, half = found.groups()
    return time(int(hour)%12+(12 if half.lower() == "p" else 0), int(minute))


def october_news(snapshots):
    events = []

    def add(day, clock, name, source):
        if clock is not None:
            events.append({"name": " ".join(name.split()), "timestamp": datetime.combine(day, clock, ET).isoformat(),
                           "revision": None, "source_id": source})

    tree = Tree(gzip.decompress((snapshots/"bls-october.html.gz").read_bytes()).decode()).root
    for cell in tree.find("td"):
        match = re.fullmatch(r"d10(\d{2})", cell.attrs.get("id", ""))
        if match:
            day = date(2026, 10, int(match[1]))
            for paragraph in cell.find("p"):
                names = list(paragraph.find("strong"))
                if names:
                    add(day, release_clock(paragraph.text()), names[0].text(), "bls-october")
    tree = Tree(gzip.decompress((snapshots/"fed-october.html.gz").read_bytes()).decode()).root
    for clock_node in tree.find("div", **{"class": "col-xs-2"}):
        clock = release_clock(clock_node.text())
        if clock:
            row = clock_node.parent
            names, days = list(row.find("div", **{"class": "col-xs-7"})), list(row.find("div", **{"class": "col-xs-3"}))
            if names and days:
                for number in re.findall(r"\b\d{1,2}\b", days[0].text()):
                    add(date(2026, 10, int(number)), clock, names[0].text(), "fed-october")
    tree = Tree(gzip.decompress((snapshots/"bea-full.html.gz").read_bytes()).decode()).root
    for row in tree.find("tr"):
        cells = list(row.find("td"))
        if not cells:
            continue
        release_date = list(cells[0].find("div", **{"class": "release-date"}))
        if not release_date:
            continue
        match = re.fullmatch(r"\s*October\s+(\d{1,2})\s*", release_date[0].text())
        titles = [n for n in cells if "release-title" in n.attrs.get("class", "").split()]
        if match and titles:
            add(date(2026, 10, int(match[1])), release_clock(cells[0].text()), titles[0].text(), "bea-full")
    return list({(r["timestamp"], r["name"]): r for r in events}.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cme-hours", required=True)
    parser.add_argument("--cme-spec", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    destination = Path(args.out)
    if destination.exists():
        raise ValueError("Output already exists; preserve the prior frozen version")
    source = ROOT/"ninjatrader/calendars/mnq-dec26-sim101-2026-10-07-09/calendar.json"
    raw = json.loads(source.read_text())
    payload = json.loads(Path(args.cme_hours).read_text())
    product = payload["products"][0]
    if len(payload["products"]) != 1 or product["id"] != 8668 or product["globex"] != "MNQ":
        raise ValueError("Dated hours do not identify only the actual CME MNQ product")
    central = ZoneInfo("America/Chicago")
    opens, closes, omitted = {}, {}, []
    for schedule in product["tradingHours"]["schedules"]:
        event_day = date.fromisoformat(schedule["eventDate"])
        for event in schedule["events"]:
            if event["marketEventType"] not in {"open", "closed", "preopen"}:
                raise ValueError("Unmodeled CME event; review the special session before exporting")
            stamp = datetime.combine(event_day, time.fromisoformat(event["eventTime"]), central).astimezone(ET)
            trade_day = date.fromisoformat(event["tradingDate"])
            if event["marketEventType"] == "open":
                if trade_day in opens:
                    if trade_day >= date(2026, 10, 7):
                        raise ValueError("Multiple enabled-session opens require explicit reviewed intraday breaks")
                    omitted.append({"trading_date": str(trade_day), "omitted_open": opens[trade_day].isoformat(),
                                    "reason": "Historical holiday multi-open segment is excluded; only the final prior-evening segment supplies this warmup session."})
                    stamp = max(opens[trade_day], stamp)
                opens[trade_day] = stamp
            elif event["marketEventType"] == "closed":
                closes[trade_day] = stamp
    spec = json.loads(Path(args.cme_spec).read_text())
    assert spec["ProductID"] == 8668
    all_news = october_news(ROOT/"ninjatrader/calendars/source-snapshots")
    rows = {date.fromisoformat(r["date"]): r for r in raw["sessions"]}
    day = date(2026, 10, 12)
    while day <= date(2026, 10, 30):
        if day.weekday() < 5:
            rows[day] = {"date": str(day), "open": datetime.combine(day, time(9,30), ET).isoformat(),
                         "close": datetime.combine(day, time(16), ET).isoformat(), "contract": "MNQ 12-26",
                         "roll_day": False, "trade_enabled": True, "news": {"flags": [], "releases": []}}
        day += timedelta(days=1)
    existing_news = [deepcopy(r) for row in rows.values() for r in row["news"]["releases"]]
    combined = {(r["timestamp"], r["name"]): r for r in existing_news+all_news}
    for day, row in sorted(rows.items()):
        if day not in opens or day not in closes:
            raise ValueError(f"No dated CME session for {day}")
        row["globex"] = {"open": opens[day].isoformat(), "close": closes[day].isoformat(), "breaks": []}
        selected = [r for r in combined.values() if opens[day] <= datetime.fromisoformat(r["timestamp"]) < closes[day]]
        row["news"]["releases"] = sorted(selected, key=lambda r: (r["timestamp"], r["name"]))
        row["news"]["flags"] = sorted(set(row["news"]["flags"]) | {r.get("source_id", "scheduled_macro") for r in selected})
    now = datetime.now(timezone.utc).isoformat()
    snapshots = ROOT/"ninjatrader/calendars/source-snapshots"
    for filename, path, url in (
        ("cme-mnq-dated-hours-2026-08-23-10-30.json.gz", Path(args.cme_hours), "https://www.cmegroup.com/services/trading-hours-by-product?pageNumber=1&pageSize=100&exch=CME&cleared=Futures&searchString=Micro%20E-mini%20Nasdaq&fromEventDate=2026-08-23&toEventDate=2026-10-30"),
        ("cme-mnq-contract-spec.json.gz", Path(args.cme_spec), "https://www.cmegroup.com/CmeWS/mvc/ContractSpecs/List/productId/8668"),
    ):
        data = path.read_bytes()
        target = snapshots/filename
        if target.exists() and gzip.decompress(target.read_bytes()) != data:
            raise ValueError("Preserve an existing differing source snapshot")
        target.write_bytes(gzip.compress(data, mtime=0))
        raw["sources"].append({"id": filename.removesuffix(".gz"), "url": url, "retrieved_at": now, "sha256": hashlib.sha256(data).hexdigest()})
    raw["sessions"] = [row for _, row in sorted(rows.items())]
    raw["source"] += "; official dated CME MNQ Globex hours and contract specification"
    raw["purpose"] = "R2 full-session Sim101 paper experiment for futures trading dates October 7-30, 2026. Earlier rows are cash ATR20 warmup only. Review/refresh later dates before trading."
    raw["globex_policy"] = "Use dated CME schedules, not ClearPort/preopen hours. Futures trading date advances at 18:00 ET; flatten five minutes before each reviewed close. No midnight loss-budget reset."
    raw["revision_policy"] = "Retained official schedules are frozen snapshots, not a live news service. Historical warmup hours were reviewed retrospectively; forward R2 begins only after installation. Release revision markers are unknown (null). Preserve the snapshot and refresh schedules before later trading dates; do not treat it as point-in-time research data."
    raw["full_session_hours_as_of"] = now
    raw["historical_globex_segments_excluded"] = omitted
    destination.mkdir(parents=True)
    output = destination/"calendar.json"
    output.write_text(json.dumps(raw, indent=2)+"\n")
    calendar = Calendar(output)
    print(f"Reviewed CME calendar exported: {len(calendar.sessions)} sessions; {sum(s.trade_enabled for s in calendar.sessions.values())} enabled; {len(all_news)} timed October macro events.")


if __name__ == "__main__":
    main()
