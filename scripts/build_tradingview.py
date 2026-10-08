"""Render the Pine port with the repository's reviewed calendar and loss questions."""

import ast
import hashlib
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CALENDAR = ROOT / "ninjatrader/calendars/mnq-dec26-full-session-2026-10-07-30/calendar.json"
TEMPLATE = ROOT / "tradingview/MNQ_R2_Paper.template.pine"
OUTPUT = ROOT / "tradingview/MNQ_R2_Paper.pine"


def milliseconds(value):
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def questions():
    tree = ast.parse((ROOT / "bot/review.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "diagnose")
    result = []
    for node in function.body:
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == "add"):
            q = node.value.args[1]
            if isinstance(q, ast.IfExp):
                q = q.body  # R2 wording.
            result.append(ast.literal_eval(q))
    if len(result) != 30:
        raise ValueError("Expected the engine's 30 loss diagnostics")
    return result


def render():
    raw = CALENDAR.read_bytes()
    calendar = json.loads(raw)
    if calendar.get("synthetic") or len(calendar["sessions"]) != 49:
        raise ValueError("Expected reviewed full-session calendar")
    data = {k: [] for k in ("calDay", "cashOpen", "cashClose", "marketOpen", "marketClose", "enabled", "newsStart", "newsEnd", "breakStart", "breakEnd")}
    for row in calendar["sessions"]:
        if row["contract"] != "MNQ 12-26":
            raise ValueError("Pine port is frozen to MNQ December 2026")
        data["calDay"].append(milliseconds(row["date"] + "T00:00:00-04:00"))
        for key, value in (("cashOpen", row["open"]), ("cashClose", row["close"]), ("marketOpen", row["globex"]["open"]), ("marketClose", row["globex"]["close"])):
            data[key].append(milliseconds(value))
        data["enabled"].append(bool(row["trade_enabled"] and not row["roll_day"] and row["close"][11:16] == "16:00"))
        for event in row["news"]["releases"]:
            release = milliseconds(event["timestamp"])
            start, end = max(data["marketOpen"][-1], release - 300000), min(data["marketClose"][-1], release + 600000)
            if start < end:
                data["newsStart"].append(start)
                data["newsEnd"].append(end)
        for start, end in row["globex"]["breaks"]:
            data["breakStart"].append(milliseconds(start))
            data["breakEnd"].append(milliseconds(end))
    lines = ["// Reviewed calendar SHA256: " + hashlib.sha256(raw).hexdigest()]
    for key, values in data.items():
        kind = "bool" if key == "enabled" else "int"
        contents = ", ".join(str(v).lower() for v in values)
        if values:
            lines.append(f"var array<{kind}> {key} = array.from({contents})")
        else:
            lines.append(f"var array<{kind}> {key} = array.new<{kind}>()")
    lines.append("var array<string> questions = array.from(" + ", ".join(json.dumps(q) for q in questions()) + ")")
    template = TEMPLATE.read_text()
    if template.count("// @GENERATED_CALENDAR@") != 1:
        raise ValueError("Exactly one calendar placeholder required")
    return template.replace("// @GENERATED_CALENDAR@", "\n".join(lines))


if __name__ == "__main__":
    OUTPUT.write_text(render(), encoding="utf-8")
    print(f"Rendered {OUTPUT.relative_to(ROOT)}")
