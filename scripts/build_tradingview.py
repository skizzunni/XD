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
GUARDED_OUTPUT = ROOT / "tradingview/MNQ_R2_Guarded.pine"


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


def render(variant="baseline"):
    if variant not in {"baseline", "guarded"}:
        raise ValueError("Unknown Pine experiment variant")
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
    digest = hashlib.sha256(raw).hexdigest()
    lines = ["// Reviewed calendar SHA256: " + digest,
             'const string calendarHash = "' + digest + '"']
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
    result = template.replace("// @GENERATED_CALENDAR@", "\n".join(lines))
    if variant == "guarded":
        title = 'strategy("MNQ R2 Paper Research",'
        default = 'input.string("Baseline R2", "Paper test variant"'
        if result.count(title) != 1 or result.count(default) != 1:
            raise ValueError("Pine variant declaration mismatch")
        result = result.replace(title, 'strategy("MNQ R2 Guarded Paper Test",', 1)
        result = result.replace(default, 'input.string("Guarded R2", "Paper test variant"', 1)
    return result


if __name__ == "__main__":
    OUTPUT.write_text(render(), encoding="utf-8")
    GUARDED_OUTPUT.write_text(render("guarded"), encoding="utf-8")
    print(f"Rendered {OUTPUT.relative_to(ROOT)}")
    print(f"Rendered {GUARDED_OUTPUT.relative_to(ROOT)}")
