from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import random
import statistics
import json

from .models import TICK_VALUE


def max_drawdown(values):
    equity = peak = worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def stats(trades, extra_ticks=0, session_days=None):
    net = [t.net_ticks - extra_ticks for t in trades]
    gross = [t.gross_ticks for t in trades]
    gains = sum(v for v in net if v > 0)
    losses = -sum(v for v in net if v < 0)
    total = sum(net)
    quarter = {}
    for day in session_days or []:
        d = datetime.fromisoformat(day)
        quarter[f"{d.year}-Q{(d.month - 1) // 3 + 1}"] = 0
    for trade, value in zip(trades, net):
        d = datetime.fromisoformat(trade.session)
        key = f"{d.year}-Q{(d.month - 1) // 3 + 1}"
        quarter[key] = quarter.get(key, 0) + value
    positive_quarters = (
        sum(v > 0 for v in quarter.values()) / len(quarter) if quarter else 0
    )
    return {
        "trades": len(net),
        "gross_mean_ticks": statistics.mean(gross) if gross else 0,
        "mean_all_in_ticks": statistics.mean(t.all_in_ticks for t in trades)
        if trades
        else 0,
        "net_mean_ticks": statistics.mean(net) if net else 0,
        "net_total_ticks": total,
        "net_total_usd": total * TICK_VALUE,
        "profit_factor": gains / losses if losses else None,
        "profit_factor_infinite": bool(gains and not losses),
        "max_drawdown_ticks": max_drawdown(net),
        "max_drawdown_usd": max_drawdown(net) * TICK_VALUE,
        "largest_trade_share": max(net, default=0) / total if total > 0 else None,
        "positive_quarter_fraction": positive_quarters,
        "quarters": quarter,
    }


def block_bootstrap(trades, session_days, samples=2000, block_size=5, seed=1729):
    if not trades:
        return [None, None]
    by_day = {d: [] for d in session_days}
    for t in trades:
        by_day.setdefault(t.session, []).append(t.net_ticks)
    days = list(by_day)
    rng, means = random.Random(seed), []
    # Stationary bootstrap over chronological sessions, retaining no-trade sessions.
    for _ in range(samples):
        index, selected = rng.randrange(len(days)), []
        for _ in days:
            selected.extend(by_day[days[index]])
            index = (
                rng.randrange(len(days))
                if rng.random() < 1 / block_size
                else (index + 1) % len(days)
            )
        if selected:
            means.append(statistics.mean(selected))
    if not means:
        return [None, None]
    means.sort()
    return [means[int(0.025 * (len(means) - 1))], means[int(0.975 * (len(means) - 1))]]


def gates(metrics, ci, validation, pbo, config, complete=True):
    pf_ok = metrics["profit_factor_infinite"] or (metrics["profit_factor"] or 0) > 1.10
    return {
        "actual_costs_calibrated": config.costs_calibrated,
        "minimum_150_trades": metrics["trades"] >= config.minimum_segment_trades,
        "gross_at_least_twice_base_cost": metrics["gross_mean_ticks"]
        >= 2 * max(config.all_in_ticks, metrics["mean_all_in_ticks"]),
        "net_at_least_two_ticks": metrics["net_mean_ticks"] >= 2,
        "profit_factor_above_1_10": pf_ok,
        "bootstrap_lower_bound_positive": ci[0] is not None and ci[0] > 0,
        "pbo_at_most_25_percent": pbo is not None and pbo <= 0.25,
        "no_trade_above_20_percent_profit": metrics["largest_trade_share"] is not None
        and metrics["largest_trade_share"] <= 0.20,
        "55_percent_quarters_positive": metrics["positive_quarter_fraction"] >= 0.55,
        "drawdown_within_budget": metrics["max_drawdown_usd"]
        <= config.research_loss_budget_usd,
        "drawdown_vs_validation": validation is None
        or metrics["max_drawdown_ticks"] <= 1.25 * validation["max_drawdown_ticks"],
        "plus_one_tick_per_side_positive": metrics["net_mean_ticks"] - 2 > 0,
        "data_complete": complete,
    }


def engine_report(engine, synthetic=False):
    metrics = stats(engine.trades)
    by_side = {
        name: stats([t for t in engine.trades if t.direction == direction])
        for name, direction in (("long", 1), ("short", -1))
    }
    news = {
        "news": stats([t for t in engine.trades if t.news_flags]),
        "no_news": stats([t for t in engine.trades if not t.news_flags]),
    }
    submitted = sum(
        e["reason"] == "SUBMITTED" and ":entry" in e.get("order_id", "")
        for e in engine.events
    )
    missed = sum(e["reason"] == "MISSED" for e in engine.events)
    rejected = sum(e["reason"] == "REJECTED" for e in engine.events)
    faults = [
        e
        for e in engine.events
        if e["reason"]
        in {
            "DATA_GAP",
            "BAD_TIMESTAMP",
            "DUPLICATE_TICK",
            "UNRESOLVED_CONTRACT",
            "UNRESOLVED_POSITION",
            "FAULT_EXIT",
        }
    ]
    return {
        "mode": "paper",
        "strategy": engine.config.strategy,
        "synthetic_data": synthetic,
        "performance_evidence": not synthetic,
        "costs_calibrated": engine.config.costs_calibrated,
        "base_cost_floor_ticks": engine.config.all_in_ticks,
        "metrics": metrics,
        "by_side": by_side,
        "by_news": news,
        "cost_stress": {
            "base_minus_1_tick_per_side": stats(engine.trades, -2),
            "base": metrics,
            "base_plus_1_tick_per_side": stats(engine.trades, 2),
        },
        "worksheet": {
            str(cost): {
                "net_mean_ticks": metrics["gross_mean_ticks"] - cost,
                "gross_hurdle_ticks": 2 * cost,
            }
            for cost in (4, 5, 6)
        },
        "execution": {
            "submitted_entries": submitted,
            "missed": missed,
            "rejected": rejected,
            "fill_rate": len(engine.trades) / (submitted + missed + rejected)
            if submitted + missed + rejected
            else 0,
            "fault_events": len(faults),
        },
        "session_count": len(engine.session_records),
        "eligible_sessions": sum(
            bool(s["eligibility"] == "ELIGIBLE" and s["complete"] and s["atr20"])
            for s in engine.session_records
        ),
        "event_hash": engine.event_hash(),
        "trade_count_cap": None if engine.config.strategy == "R1" else 1,
        "learning_samples": len(engine.learner.samples),
        "decision": "SYNTHETIC_SMOKE_TEST_ONLY"
        if synthetic
        else "RESEARCH_GATES_NOT_YET_EVALUATED",
    }


def write_json(path, content):
    Path(path).write_text(
        json.dumps(content, indent=2, sort_keys=True, default=str, allow_nan=False)
        + "\n"
    )


def write_run(engine, directory, provenance):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "manifest.json", provenance)
    report = engine_report(engine, engine.calendar.synthetic)
    report["completed"] = engine.finalized
    if not engine.finalized:
        report["decision"] = "FAILED_RUN_DO_NOT_USE_FOR_PERFORMANCE"
    write_json(directory / "report.json", report)
    write_json(
        directory / "open_position.json",
        asdict(engine.broker.position) if engine.broker.position else None,
    )
    write_json(directory / "trades.json", [asdict(t) for t in engine.trades])
    from .review import diagnose, learning_queue

    losses = [
        diagnose(t, a, engine.events, engine.config)
        for t, a in zip(engine.trades, engine.trade_audits)
        if t.net_ticks < 0
    ]
    write_json(directory / "loss_reviews.json", losses)
    write_json(
        directory / "paper_experiments.json", learning_queue(losses, len(engine.trades))
    )
    write_json(directory / "trade_audits.json", engine.trade_audits)
    write_json(directory / "learning_state.json", engine.learner.samples)
    with (directory / "events.jsonl").open("w") as f:
        for event in engine.events:
            f.write(
                json.dumps(event, sort_keys=True, default=str, allow_nan=False) + "\n"
            )
