"""Chronological selection with immutable provenance and a consumed-once holdout."""

from dataclasses import replace
import itertools
import json
import math
from pathlib import Path
import statistics

from .backtest import provenance, replay
from .data import Calendar, read_ticks
from .models import Config
from .report import block_bootstrap, gates, stats, write_json, write_run


def primary_candidates(base):
    # P0 is one of the nine frozen grid pairs; do not count duplicate 0.10/0.10 as a new test.
    return [
        replace(base, strategy="P0", long_threshold=l, short_threshold=s)
        for l in (0.08, 0.10, 0.12)
        for s in (0.10, 0.12, 0.14)
    ]


def pbo(results, days, blocks=8):
    if len(results) < 2 or len(days) < blocks:
        return None
    chunks = [
        days[i * len(days) // blocks : (i + 1) * len(days) // blocks]
        for i in range(blocks)
    ]
    observations = []
    for engine in results:
        by_day = {day: [] for day in days}
        for t in engine.trades:
            by_day[t.session].append(t.net_ticks)
        observations.append(by_day)
    overfit = 0
    combinations = list(itertools.combinations(range(blocks), blocks // 2))
    for selection in combinations:
        inside = {d for i in selection for d in chunks[i]}
        outside = set(days) - inside

        def score(candidate, subset):
            values = [
                v for d in days if d in subset for v in observations[candidate][d]
            ]
            return statistics.mean(values) if values else -math.inf

        winner = max(
            range(len(results)),
            key=lambda i: (
                score(i, inside),
                -stats([t for t in results[i].trades if t.session in inside])[
                    "max_drawdown_ticks"
                ],
                -i,
            ),
        )
        scores = [score(i, outside) for i in range(len(results))]
        # Midrank handles identical OOS candidates conservatively.
        rank = (
            sum(s < scores[winner] for s in scores)
            + 0.5 * sum(s == scores[winner] for s in scores)
        ) / len(scores)
        overfit += rank <= 0.5
    return overfit / len(combinations)


def prepare(ticks_path, calendar_path, config, directory):
    if config.daily_profit_target_usd or config.break_even_trigger_r:
        raise ValueError(
            "Research family is the baseline; evaluate account overlays in separate frozen runs"
        )
    calendar = Calendar(calendar_path)
    if calendar.synthetic:
        raise ValueError(
            "Synthetic demo data cannot be used for strategy promotion or holdout"
        )
    if not config.costs_calibrated:
        raise ValueError(
            "Enter actual account costs and set costs_calibrated=true before research selection"
        )
    # Inspect dates only; do not calculate holdout signals, fills, returns or P&L.
    available = set()
    for tick in read_ticks(ticks_path):
        calendar.for_tick(tick)
        available.add(tick.timestamp.date())
    sessions = sorted(
        d for d in available if calendar.sessions[d].eligibility == "ELIGIBLE"
    )
    # First twenty full RTH sessions provide lagged ATR. Further data faults remain explicit gates.
    first_possible = sorted(available)[20:]
    sessions = [d for d in sessions if d in first_possible]
    if len(sessions) < 4:
        raise ValueError("Not enough sessions for ATR warmup and chronological split")
    a, b = len(sessions) // 2, 3 * len(sessions) // 4
    parts = {
        "development": [str(d) for d in sessions[:a]],
        "validation": [str(d) for d in sessions[a:b]],
        "holdout": [str(d) for d in sessions[b:]],
    }
    if (
        min(len(parts["validation"]), len(parts["holdout"]))
        < config.minimum_segment_trades
    ):
        raise ValueError(
            "Each validation and holdout needs at least 150 eligible trades; extend the data sample"
        )
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    manifest = provenance(ticks_path, calendar_path, config)
    manifest["partitions"] = parts
    manifest["status"] = "PREPARED"
    write_json(directory / "frozen.json", manifest)
    return manifest


def verify(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "frozen.json").read_text())
    cfg = Config(**manifest["config"])
    current = provenance(manifest["ticks_path"], manifest["calendar_path"], cfg)
    for key in (
        "code_hash",
        "tick_checksum",
        "calendar_checksum",
        "transformation_version",
        "config",
    ):
        if current[key] != manifest[key]:
            raise ValueError(
                f"Frozen {key} changed; create a new candidate version, do not reuse this holdout"
            )
    return manifest, cfg


def run_phase(directory, phase):
    directory = Path(directory)
    manifest, cfg = verify(directory)
    output = directory / f"{phase}.json"
    if output.exists():
        raise ValueError(f"{phase} already completed for this frozen version")
    if phase == "development":
        days = manifest["partitions"][phase]
        candidates = primary_candidates(cfg)
        engines = [
            replay(
                manifest["ticks_path"], manifest["calendar_path"], c, days[0], days[-1]
            )
            for c in candidates
        ]
        idx = max(
            range(len(engines)),
            key=lambda i: (
                stats(engines[i].trades)["net_mean_ticks"],
                -stats(engines[i].trades)["max_drawdown_ticks"],
                -i,
            ),
        )
        primary_pbo = pbo(engines, days, cfg.pbo_blocks)
        challenger = replace(cfg, strategy="C1")
        c1 = replay(
            manifest["ticks_path"],
            manifest["calendar_path"],
            challenger,
            days[0],
            days[-1],
        )
        # C1 has no selected/optimized parameters; PBO is not estimated from one configuration.
        result = {
            "P0": {
                "config": candidates[idx].to_dict(),
                "pbo": primary_pbo,
                "candidates": [
                    {"config": c.to_dict(), "metrics": stats(e.trades)}
                    for c, e in zip(candidates, engines)
                ],
                "metrics": stats(engines[idx].trades),
            },
            "C1": {
                "config": challenger.to_dict(),
                "pbo": None,
                "selection_risk": "NOT_ESTIMABLE_SINGLE_CONFIGURATION",
                "metrics": stats(c1.trades),
            },
        }
    elif phase in {"validation", "holdout"}:
        dev_path = directory / "development.json"
        if not dev_path.exists():
            raise ValueError("Complete development before validation/holdout")
        dev = json.loads(dev_path.read_text())
        validation = None
        if phase == "holdout":
            if not (directory / "validation.json").exists():
                raise ValueError("Complete validation before holdout")
            validation = json.loads((directory / "validation.json").read_text())
            if not any(all(v["gates"].values()) for v in validation.values()):
                raise ValueError(
                    "No candidate passed every validation gate; holdout stays untouched"
                )
            # Exclusive file creation consumes the version BEFORE performance execution, including crashes.
            with (directory / "HOLDOUT_CONSUMED").open("x") as lock:
                lock.write(manifest["code_hash"] + "\n")
        days = manifest["partitions"][phase]
        result = {}
        for family in ("P0", "C1"):
            if phase == "holdout" and not all(validation[family]["gates"].values()):
                continue
            selected = Config(**dev[family]["config"])
            engine = replay(
                manifest["ticks_path"],
                manifest["calendar_path"],
                selected,
                days[0],
                days[-1],
            )
            metrics = stats(engine.trades, session_days=days)
            ci = block_bootstrap(
                engine.trades,
                days,
                cfg.bootstrap_samples,
                cfg.bootstrap_block_sessions,
                cfg.seed,
            )
            reports = [s for s in engine.session_records if s["session"] in days]
            complete = len(reports) == len(days) and all(s["complete"] for s in reports)
            risk = dev[family]["pbo"]
            checks = gates(
                metrics,
                ci,
                validation[family]["metrics"] if validation else None,
                risk,
                cfg,
                complete,
            )
            if family == "C1":
                # Conservatively leave promotion blocked: the spec's numeric PBO gate has no estimate.
                checks["pbo_at_most_25_percent"] = False
            selected_events = [e for e in engine.events if e["session"] in days]
            execution = {
                "reject_rate": sum(e["reason"] == "REJECTED" for e in selected_events)
                / len(days),
                "fault_rate": sum(
                    e["reason"]
                    in {
                        "DATA_GAP",
                        "DATA_QUALITY",
                        "FAULT_EXIT",
                        "BAD_TIMESTAMP",
                        "DUPLICATE_TICK",
                    }
                    for e in selected_events
                )
                / len(days),
            }
            result[family] = {
                "config": selected.to_dict(),
                "metrics": metrics,
                "ci95_net_ticks": ci,
                "execution": execution,
                "gates": checks,
                "decision": "CONTINUE_SIMULATION"
                if all(checks.values())
                else "REJECT_OR_EXTEND",
                "stress_plus_one_tick_per_side": stats(engine.trades, 2),
            }
            write_run(
                engine,
                directory / f"{phase}-{family}",
                {**manifest, "selected_config": selected.to_dict()},
            )
        if "P0" in result and "C1" in result:
            a, b = result["P0"]["metrics"], result["C1"]["metrics"]
            result["C1"]["relative_gate"] = (
                b["net_mean_ticks"] >= a["net_mean_ticks"] + 1
                and b["max_drawdown_ticks"] <= a["max_drawdown_ticks"]
                and result["C1"]["execution"]["reject_rate"]
                <= result["P0"]["execution"]["reject_rate"]
                and result["C1"]["execution"]["fault_rate"]
                <= result["P0"]["execution"]["fault_rate"]
            )
    else:
        raise ValueError("Unknown research phase")
    write_json(output, result)
    return result
