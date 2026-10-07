"""Evidence-based loss diagnostics and quarantined paper experiments, not auto-fitting."""

from collections import Counter
from dataclasses import asdict

from .models import TICK, TICK_VALUE


def efficiency(bars):
    if not bars:
        return None
    path = abs(bars[0]["close"] - bars[0]["open"]) + sum(
        abs(b["close"] - a["close"]) for a, b in zip(bars, bars[1:])
    )
    return abs(bars[-1]["close"] - bars[0]["open"]) / path if path else 0


def diagnose(trade, audit, events, config):
    events = [
        e
        for e in events
        if e["session"] == trade.session
        and trade.entry_time <= e["timestamp"] <= trade.exit_time
    ]
    reasons = {e["reason"] for e in events}
    checks = []

    def add(category, question, answer, status="info", evidence=None):
        checks.append(
            {
                "number": len(checks) + 1,
                "category": category,
                "question": question,
                "answer": answer,
                "status": status,
                "evidence": evidence,
            }
        )

    atr = audit.get("atr20")
    er = efficiency(audit.get("pre_entry_bars", []))
    mfe, mae = audit.get("max_net_ticks"), audit.get("min_net_ticks")
    quotes = audit.get("quoted_entry") and audit.get("quoted_exit")
    stop_events = [e for e in events if e["reason"] == "STOP_ACTIVE"]
    fills = [e for e in events if e["reason"] == "FILLED"]
    entry_fill = next((e for e in fills if ":entry" in e.get("order_id", "")), None)
    immediate = bool(
        stop_events
        and entry_fill
        and stop_events[0]["timestamp"] == entry_fill["timestamp"]
    )
    fault_reasons = reasons & {
        "DATA_GAP",
        "DUPLICATE_TICK",
        "BAD_TIMESTAMP",
        "UNRESOLVED_CONTRACT",
        "FAULT_EXIT",
    }
    stop_gap = None
    if trade.exit_reason == "EMERGENCY_STOP" and "final_stop" in audit:
        stop_gap = max(
            0, trade.direction * (audit["final_stop"] - trade.exit_fill) / TICK
        )
    add(
        "System",
        "Did the setup meet all mechanical entry criteria?",
        "Recorded entry passed configured guards.",
        "pass" if audit else "unknown",
        {
            "signal": audit.get("signal_direction"),
            "m": audit.get("m"),
            "calendar": audit.get("calendar_reason"),
            "rolling_efficiency": audit.get("rolling_efficiency"),
            "learning_at_entry": audit.get("learning_at_entry"),
        },
    )
    add(
        "System",
        "Was the signal based on the required completed bars?",
        "Six completed opening bars retained."
        if config.strategy == "P0"
        else "R1/R2 use six closed rolling bars and a fresh pullback breakout."
        if config.strategy in {"R1", "R2"}
        else "C1 uses three completed formation bars.",
        "pass"
        if config.strategy == "P0" and len(audit.get("opening_bars", [])) == 6
        else "info",
    )
    add(
        "System",
        "Was ATR20 lagged and based only on completed prior sessions?",
        "Engine uses prior-session ATR20.",
        "pass" if atr else "unknown",
        {"atr20": atr},
    )
    add(
        "Calendar",
        "Was this a reviewed full futures session?" if config.strategy == "R2" else "Was this a full eligible RTH session?",
        audit.get("calendar_reason", "Unknown"),
        "pass" if audit.get("calendar_reason") == "ELIGIBLE" else "flag",
    )
    add(
        "Calendar",
        "Did scheduled news fall inside the forbidden closing window?",
        "No forbidden release in supplied frozen calendar; independent completeness is unverified.",
        "info",
        {"flags": trade.news_flags},
    )
    add(
        "Contract",
        "Was the contract explicitly mapped and was rollover excluded?",
        trade.contract,
        "pass" if "UNRESOLVED_CONTRACT" not in reasons else "flag",
    )
    add(
        "Data",
        "Were there timestamp, duplicate, stale-feed or coverage faults before exit?",
        ", ".join(sorted(fault_reasons)) or "No detected fault.",
        "flag" if fault_reasons else "pass",
    )
    add(
        "Risk",
        "Was the stop resting on a broker server?",
        audit.get("stop_implementation", "Unknown"),
        "unknown",
    )
    add(
        "Risk",
        "Was protection activated immediately after entry?",
        "Same recorded tick as entry." if immediate else "Cannot confirm.",
        "pass" if immediate else "unknown",
    )
    add(
        "Risk",
        "Was quantity within the selected paper-contract setting?",
        str(audit.get("position_quantity", "Unknown")),
        "pass" if audit.get("position_quantity") == config.paper_contracts else "unknown",
    )
    add(
        "Emotion",
        "Were contracts added to a losing position to break even faster?",
        "No averaging or pyramiding route exists in this engine.",
        "pass",
    )
    add(
        "Execution",
        "Did entry exceed the configured chase/slippage cap?",
        "Recorded adverse entry ticks.",
        "flag"
        if audit.get("entry_adverse_ticks", 0) > config.slippage_cap_ticks
        else "pass",
        {"actual": audit.get("entry_adverse_ticks"), "cap": config.slippage_cap_ticks},
    )
    add(
        "Costs",
        "Were fees verified against the actual account schedule?",
        "Verified configuration flag."
        if config.costs_calibrated
        else "Fees remain provisional.",
        "pass" if config.costs_calibrated else "unknown",
        {"fees_usd": trade.fees_usd},
    )
    add(
        "Costs",
        "Was spread observed rather than estimated or counted twice?",
        "Bid/ask at both fills; no extra synthetic spread."
        if quotes
        else "Frozen spread model used for one or both fills.",
        "pass" if quotes else "unknown",
        {"execution_ticks": trade.execution_ticks},
    )
    add(
        "Costs",
        "Did costs turn a non-losing gross result into a net loss?",
        str(trade.gross_ticks >= 0 and trade.net_ticks < 0),
        "flag" if trade.gross_ticks >= 0 else "info",
        {
            "gross_ticks": trade.gross_ticks,
            "net_ticks": trade.net_ticks,
            "cost_ticks": trade.gross_ticks - trade.net_ticks,
        },
    )
    add(
        "Market",
        "Was the pre-entry market directional or choppy?",
        "Low path efficiency is a chop diagnostic, not a proven filter.",
        "flag" if er is not None and er < 0.3 else "info",
        {"12_closed_bar_efficiency": er},
    )
    add(
        "Market",
        "Was stop distance unusually small or large relative to lagged volatility?",
        "Recorded stop/ATR ratio.",
        "info",
        {
            "stop_to_atr": abs(
                trade.entry_fill - audit.get("initial_stop", trade.entry_fill)
            )
            / atr
            if atr
            else None
        },
    )
    add(
        "Path",
        "How adverse did the trade become before exit?",
        "Net marked-to-exit adverse excursion.",
        "info",
        {"mae_net_ticks": mae},
    )
    add(
        "Path",
        "Was the trade profitable at any observed tick before losing?",
        str(mfe is not None and mfe > 0),
        "flag" if mfe is not None and mfe > 0 else "info",
        {"mfe_net_ticks": mfe},
    )
    add(
        "Risk",
        "Was break-even protection disabled, never reached, or active?",
        "Active" if audit.get("break_even_armed") else "Not armed",
        "info",
        {
            "enabled": bool(config.break_even_trigger_r),
            "trigger_r": config.break_even_trigger_r,
        },
    )
    add(
        "Execution",
        "Did a stop gap/slippage defeat break-even or the emergency stop?",
        "Stop-to-fill adverse ticks including modeled exit slippage.",
        "flag"
        if stop_gap is not None and stop_gap > config.slippage_ticks_per_side
        else "info",
        {"stop_gap_ticks": stop_gap},
    )
    add(
        "Exit",
        "Was the loss caused at a time exit, stop, target, or fault exit?",
        trade.exit_reason,
        "flag" if trade.exit_reason == "FAULT_EXIT" else "info",
    )
    add(
        "Frequency",
        "Was this one fill for a fresh setup rather than a duplicate entry?",
        "Entry fill count for this trade; R1/R2 permit multiple distinct setups.",
        "pass" if len(fills) == 1 else "flag",
        {"entry_fills": len(fills)},
    )
    add(
        "Risk",
        "Did the realized loss exceed the configured session loss budget?",
        str(
            -audit.get("day_realized_after", trade.net_ticks * TICK_VALUE*trade.quantity)
            > config.session_loss_budget_usd
        ),
        "flag"
        if -audit.get("day_realized_after", trade.net_ticks * TICK_VALUE*trade.quantity)
        > config.session_loss_budget_usd
        else "pass",
        {
            "day_net_usd": audit.get(
                "day_realized_after", trade.net_ticks * TICK_VALUE*trade.quantity
            ),
            "budget_usd": config.session_loss_budget_usd,
        },
    )
    add(
        "Account",
        "Was the correct evaluation/funded target policy active?",
        "No daily target"
        if not config.daily_profit_target_usd
        else "Daily target configured",
        "info",
        {"profit_target_usd": config.daily_profit_target_usd},
    )
    add(
        "Account",
        "Was the firm's trailing drawdown or consistency rule breached?",
        "Firm/account rules and external account equity have not been supplied.",
        "unknown",
    )
    add(
        "Emotion",
        "Was a manual override or discretionary filter applied?",
        "Replay has no manual order interface.",
        "pass",
    )
    add(
        "Execution",
        "Could latency, order rejection or partial fills explain the loss?",
        "This engine simulates atomic one-contract fills; latency and broker state are unmeasured.",
        "unknown",
    )
    add(
        "Comparison",
        "Does this loss recur on this side, quarter or news regime?",
        "Aggregate review is required; one trade cannot establish recurrence.",
        "info",
        {
            "side": "long" if trade.direction > 0 else "short",
            "news_flags": trade.news_flags,
            "session": trade.session,
        },
    )
    add(
        "Research",
        "Would a proposed fix generalize to untouched future sessions?",
        "Unknown until chronological validation, holdout, cost stress and forward simulation.",
        "unknown",
    )
    contributors = []
    if trade.gross_ticks < 0:
        contributors.append("Observed price movement opposed the entry direction.")
    if trade.gross_ticks >= 0:
        contributors.append("Recorded execution costs/fees erased the gross result.")
    if stop_gap is not None and stop_gap > config.slippage_ticks_per_side:
        contributors.append(
            "The recorded stop fill was worse than the stop plus scheduled slippage."
        )
    if fault_reasons:
        contributors.append(
            "A detected data/order-path fault requires reconciliation before performance interpretation."
        )
    return {
        "trade": asdict(trade),
        "diagnostics": checks,
        "observed_contributors": contributors,
        "unknown_answers": sum(c["status"] == "unknown" for c in checks),
        "causal_claim": "Diagnostics describe observations; they do not prove why a market moved.",
        "active_strategy_changed": False,
        "learning_at_entry": audit.get("learning_at_entry"),
        "adaptation_policy": "R1/R2 use bounded filters on completed results; R2 separates overnight/daytime learning; broad strategy changes remain separate experiments"
        if config.strategy in {"R1", "R2"}
        else "Frozen baseline",
    }


def learning_queue(reviews, total_trades):
    flags = Counter(
        c["question"]
        for r in reviews
        for c in r["diagnostics"]
        if c["status"] == "flag"
    )
    return {
        "total_trades": total_trades,
        "losses": len(reviews),
        "recurring_flags": dict(flags),
        "minimum_observations_for_experiments": 30,
        "status": "COLLECT_MORE_SESSIONS"
        if total_trades < 30
        else "PAPER_EXPERIMENTS_ONLY",
        "active_strategy_changed": False,
        "experiments": [
            {
                "name": "Frozen P0 threshold grid",
                "implementation": "main.py research",
                "parameters": {"long": [0.08, 0.10, 0.12], "short": [0.10, 0.12, 0.14]},
            },
            {
                "name": "Break-even overlay comparison",
                "implementation": "replay identical ticks with baseline config.json and funded config.funded-paper.json --strategy P0",
                "selection": "Development only; freeze winning account policy before new validation/holdout.",
            },
            {
                "name": "C1 independent challenger",
                "implementation": "main.py replay --strategy C1",
                "promotion": "Requires absolute/relative gates and at least 30 forward-sim eligible sessions.",
            },
        ],
        "promotion_requirements": [
            "actual costs",
            "at least 150 validation trades",
            "at least 150 holdout trades",
            "one frozen holdout",
            "bootstrap lower bound >0",
            "PBO <=25% where estimable",
            "cost stress",
            "drawdown budget",
            "30 reconciled forward-sim sessions",
        ],
        "rule": "Never rewrite the active strategy from one losing trade or optimize on the holdout.",
    }
