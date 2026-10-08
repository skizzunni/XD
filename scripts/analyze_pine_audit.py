"""Reconcile saved Pine trade-audit logs; no market data, source fitting or orders."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

NUMBERS = ("trade", "quantity", "direction", "entry", "exit", "initial_stop", "final_stop", "initial_risk_usd", "planned_risk_usd", "allowance_usd", "net_usd", "commission_usd", "mfe_emulator_usd", "mae_emulator_usd")
CONFIG = ("variant", "feed_label", "account_stage", "paper_start_et", "session_loss_budget_usd", "evaluation_target_usd", "entry_chase_ticks", "trade_cap_enabled", "trade_cap_usd", "open_guard_enabled", "open_buffer_before_min", "open_buffer_after_min", "be_enabled", "be_trigger_r", "starting_contracts", "maximum_contracts", "adaptive_sizing", "adaptive_quality", "configured_fees_roundtrip_usd", "configured_exit_slippage_ticks", "audit_version", "calendar_sha256", "symbol", "chart_session", "chart_interval", "loaded_first_bar_et")


def parse_audits(text):
    trades, seen, manifests = [], {}, {}
    for number, line in enumerate(text.splitlines(), 1):
        marker = "R2_TRADE_AUDIT|"
        if marker not in line:
            continue
        payload = line.split(marker, 1)[1].strip()
        fields = {}
        for item in payload.split("|"):
            if "=" not in item:
                raise ValueError(f"Malformed audit field on line {number}")
            key, value = item.split("=", 1)
            if key in fields:
                raise ValueError(f"Duplicate audit field on line {number}")
            fields[key] = value
        for key in (*NUMBERS, "variant", "profile", "entry_et", "exit_et", "exit_reason", "learning_eligible"):
            if key not in fields:
                raise ValueError(f"Missing {key} on audit line {number}")
        for key in NUMBERS:
            fields[key] = float(fields[key])
            if not math.isfinite(fields[key]):
                raise ValueError(f"Nonfinite {key} on line {number}")
        if (fields["quantity"] <= 0 or not fields["quantity"].is_integer()
                or fields["direction"] not in {-1, 1} or fields["commission_usd"] < 0
                or fields["planned_risk_usd"] <= 0 or fields["allowance_usd"] <= 0):
            raise ValueError(f"Invalid position/risk on line {number}")
        entry = datetime.strptime(fields["entry_et"], "%Y-%m-%d %H:%M")
        exit_time = datetime.strptime(fields["exit_et"], "%Y-%m-%d %H:%M")
        if exit_time < entry:
            raise ValueError(f"Exit precedes entry on line {number}")
        gross = fields["direction"] * (fields["exit"] - fields["entry"]) * 2 * fields["quantity"]
        if abs(fields["net_usd"] - (gross - fields["commission_usd"])) > .011:
            raise ValueError(f"MNQ price/commission/net mismatch on line {number}")
        expected_initial_risk = fields["direction"] * (fields["entry"] - fields["initial_stop"]) * 2 * fields["quantity"]
        if abs(expected_initial_risk - fields["initial_risk_usd"]) > .011:
            raise ValueError(f"Initial stop/risk mismatch on line {number}")
        if fields["planned_risk_usd"] > fields["allowance_usd"] + .011:
            raise ValueError(f"Plan exceeded risk allowance on line {number}")
        manifest = {key: fields.get(key, "UNKNOWN") for key in CONFIG}
        fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        manifests[fingerprint] = manifest
        fields["experiment_id"] = fingerprint
        identity = (fingerprint, fields["trade"])
        if identity in seen:
            if seen[identity] != fields:
                raise ValueError(f"Conflicting repeated trade on line {number}")
            continue
        seen[identity] = fields
        trades.append(fields)
    if not trades:
        raise ValueError("No R2_TRADE_AUDIT records found; copy/save Pine Logs with trade audits enabled")
    return trades, manifests


def summarize(trades):
    values = sorted(trades, key=lambda t: (t["exit_et"], t["trade"]))
    winners = [t["net_usd"] for t in values if t["net_usd"] > 0]
    losers = [t["net_usd"] for t in values if t["net_usd"] < 0]
    equity = peak = drawdown = 0
    for trade in values:
        equity += trade["net_usd"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    positive, negative = sum(winners), -sum(losers)
    return {"completed_positions": len(values), "net_usd": round(equity, 2),
            "closed_trade_drawdown_usd": round(drawdown, 2),
            "winners": len(winners), "losers": len(losers),
            "net_profit_factor": positive / negative if negative else None,
            "mean_win_usd": positive / len(winners) if winners else None,
            "mean_loss_usd": negative / len(losers) if losers else None,
            "mean_net_usd": equity / len(values),
            "exit_reasons": dict(Counter(t["exit_reason"] for t in values)),
            "losses_exceeding_planned_risk": sum(-t["net_usd"] > t["planned_risk_usd"] + .011 for t in values),
            "faulted_completions": sum(t["learning_eligible"] != "true" for t in values),
            "missing_initial_risk": sum(t["initial_risk_usd"] <= 0 for t in values)}


def analyze(text):
    trades, manifests = parse_audits(text)
    groups = defaultdict(list)
    for trade in trades:
        groups[trade["experiment_id"]].append(trade)
    return {"source_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "timestamp_basis": "Reported TradingView bar timestamps in Eastern; not tick execution timestamps",
            "model": "Strategy Tester emulator; no broker execution or causal-market proof",
            "experiments": [{"experiment_id": identity, "configuration": manifests[identity],
                             "summary": summarize(values), "profiles": {p: summarize([t for t in values if t["profile"] == p]) for p in sorted({t["profile"] for t in values})},
                             "trades": values} for identity, values in groups.items()],
            "interpretation": "Do not fit parameters to these trades; compare fixed settings on later untouched sessions. Chart intrabar drawdown can exceed closed-trade drawdown."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True, help="Plain-text copied Pine Logs")
    parser.add_argument("--out", type=Path, required=True, help="New JSON report path")
    args = parser.parse_args()
    result = analyze(args.log.read_text(encoding="utf-8-sig"))
    # Preserve prior experiments: create a new report instead of overwriting one.
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(f"Analyzed {sum(e['summary']['completed_positions'] for e in result['experiments'])} positions across {len(result['experiments'])} experiment(s): {args.out}")


if __name__ == "__main__":
    main()
