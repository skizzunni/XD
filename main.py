#!/usr/bin/env python3
"""MNQ paper-only CLI. Never connects to an account or sends real orders."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

from bot.backtest import ReplayFailure, provenance, replay
from bot.accounts import apply_account, load_accounts, update_account
from bot.data import Calendar, checksum
from bot.demo import generate, generate_rolling
from bot.models import Config
from bot.report import engine_report, write_run
from bot.research import prepare, run_phase


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--config",
        default=None,
        help="Defaults to funded paper; research defaults to baseline",
    )
    p.add_argument("--account", help="Local account label; never a live route")
    p.add_argument("--accounts-file", default="runs/accounts.json")
    commands = p.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "replay", help="Replay a complete tick file through simulated fills"
    )
    run.add_argument("--ticks", required=True)
    run.add_argument("--calendar", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--strategy", choices=["P0", "C1", "R1", "R2"])
    compare = commands.add_parser("compare-r2-exits", help="Compare fixed and trend-runner exits on identical paper ticks")
    compare.add_argument("--ticks", required=True)
    compare.add_argument("--calendar", required=True)
    compare.add_argument("--out", required=True)
    sizing=commands.add_parser("compare-r2-sizing",help="Paper exit/quantity sweep with an unchanged session loss budget")
    sizing.add_argument("--ticks",required=True)
    sizing.add_argument("--calendar",required=True)
    sizing.add_argument("--out",required=True)
    sizing.add_argument("--contracts",type=int,nargs="+",default=[1,2,5,10])
    demo = commands.add_parser(
        "demo", help="Generate synthetic fixtures and run both strategies"
    )
    demo.add_argument("--out", required=True)
    demo.add_argument("--sessions", type=int, default=32)
    research = commands.add_parser(
        "research", help="Locked development/validation/holdout protocol"
    )
    research.add_argument(
        "phase", choices=["prepare", "development", "validation", "holdout"]
    )
    research.add_argument("--out", required=True)
    research.add_argument("--ticks")
    research.add_argument("--calendar")
    accounts = commands.add_parser(
        "accounts", help="Classify paper account labels and choose the active one"
    )
    accounts.add_argument("action", choices=["list", "set", "select"])
    accounts.add_argument("name", nargs="?")
    accounts.add_argument("--stage", choices=["evaluation", "funded"])
    accounts.add_argument("--loss-limit", type=float)
    nt = commands.add_parser(
        "export-nt-calendar", help="Convert frozen calendar for the NinjaTrader adapter"
    )
    nt.add_argument("--calendar", required=True)
    nt.add_argument("--out", required=True)
    review = commands.add_parser(
        "review", help="Inspect automatic loss reviews and the paper experiment queue"
    )
    review.add_argument("--run", required=True)
    dashboard = commands.add_parser(
        "dashboard", help="Build an interactive local HTML dashboard"
    )
    source = dashboard.add_mutually_exclusive_group(required=True)
    source.add_argument("--run")
    source.add_argument("--nt-events", nargs="+")
    dashboard.add_argument("--out", required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "dashboard":
        from bot.dashboard import native_payload, run_payload, render

        payload = (
            native_payload(args.nt_events) if args.nt_events else run_payload(args.run)
        )
        print(render(payload, args.out))
        return 0
    if args.command == "review":
        folder = Path(args.run)
        print(
            json.dumps(
                {
                    "loss_reviews": json.loads(
                        (folder / "loss_reviews.json").read_text()
                    ),
                    "paper_experiments": json.loads(
                        (folder / "paper_experiments.json").read_text()
                    ),
                },
                indent=2,
            )
        )
        return 0
    if args.command == "accounts":
        if args.action == "list":
            result = load_accounts(args.accounts_file)
        else:
            if not args.name or args.action == "set" and not args.stage:
                raise ValueError("Specify an account name and --stage for set")
            result = update_account(
                args.accounts_file,
                args.name,
                args.stage if args.action == "set" else None,
                args.action == "select",
                args.loss_limit,
            )
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "export-nt-calendar":
        import csv

        calendar = Calendar(args.calendar)
        output = Path(args.out)
        if output.exists():
            raise ValueError(
                "Calendar output already exists; choose a new frozen version"
            )
        with output.open("w", newline="") as f:
            f.write(
                f"# frozen_sha256={checksum(args.calendar)}; synthetic={str(calendar.synthetic).lower()}; source={Path(args.calendar).name}\n"
            )
            writer = csv.writer(f, lineterminator="\n")
            extended = any(s.globex_open is not None for s in calendar.sessions.values())
            writer.writerow(
                [
                    "date",
                    "open_et",
                    "close_et",
                    "contract",
                    "roll_day",
                    "late_news",
                    "news_flags",
                    "trade_enabled",
                    "news_windows",
                ] + (["globex_open_et", "globex_close_et", "globex_news_windows", "globex_breaks"] if extended else [])
            )
            for _, session in sorted(calendar.sessions.items()):
                writer.writerow(
                    [
                        str(session.day),
                        session.open.strftime("%H:%M"),
                        session.close.strftime("%H:%M"),
                        session.contract,
                        str(session.roll_day).lower(),
                        str(session.late_news).lower(),
                        "|".join(session.news_flags),
                        str(session.trade_enabled).lower(),
                        "|".join(
                            f"{start.strftime('%H:%M:%S')}-{end.strftime('%H:%M:%S')}"
                            for start, end in session.news_windows
                        ),
                    ] + ([
                        session.globex_open.isoformat() if session.globex_open else "",
                        session.globex_close.isoformat() if session.globex_close else "",
                        "|".join(f"{a.isoformat()}~{b.isoformat()}" for a, b in session.globex_news_windows),
                        "|".join(f"{a.isoformat()}~{b.isoformat()}" for a, b in session.globex_breaks),
                    ] if extended else [])
                )
        print(f"Frozen NinjaTrader calendar written to {output}")
        return 0
    default_path = (
        "config.json" if args.command == "research" else "config.funded-paper.json"
    )
    config = Config.load(args.config or default_path)
    if args.account or not args.config and args.command != "research":
        config, _, _ = apply_account(config, args.accounts_file, args.account)
    if args.command == "demo":
        if args.sessions < 22:
            raise ValueError(
                "Demo needs at least 22 sessions to exercise ATR20 and trades"
            )
        root = Path(args.out)
        ticks, calendar = generate(root, args.sessions)
        reports = {}
        for strategy in ("P0", "C1", "R1"):
            cfg = replace(config, strategy=strategy)
            arm_ticks, arm_calendar = (
                generate_rolling(root / "R1-fixture", args.sessions)
                if strategy == "R1"
                else (ticks, calendar)
            )
            engine = replay(arm_ticks, arm_calendar, cfg)
            write_run(engine, root / strategy, provenance(arm_ticks, arm_calendar, cfg))
            reports[strategy] = engine_report(engine, True)
        print(json.dumps(reports, indent=2, allow_nan=False))
    elif args.command in {"compare-r2-exits","compare-r2-sizing"}:
        quantities=sorted(set(args.contracts)) if args.command=="compare-r2-sizing" else [config.paper_contracts]
        if any(not 1<=q<=10 for q in quantities):raise ValueError("Paper comparison quantities must be 1-10")
        root=Path(args.out)
        root.mkdir(parents=True,exist_ok=False)
        results={}
        for quantity,profile in ((q,p) for q in quantities for p in ("Fixed","TrendRunner")):
            label=f"Q{quantity}-{profile}" if args.command=="compare-r2-sizing" else profile
            cfg=replace(config,strategy="R2",exit_profile=profile,adaptive_quality=False,adaptive_sizing=False,paper_contracts=quantity)
            try:
                engine=replay(args.ticks,args.calendar,cfg)
            except ReplayFailure as exc:
                write_run(exc.engine,root/label,{**provenance(args.ticks,args.calendar,cfg),"failed":True,"error":str(exc)})
                raise
            write_run(engine,root/label,provenance(args.ticks,args.calendar,cfg))
            results[label]=engine_report(engine,engine.calendar.synthetic)
        comparison={"profiles":results,"tick_checksum":checksum(args.ticks),"calendar_checksum":checksum(args.calendar),
                    "adaptive_quality":False,"adaptive_sizing":False,"automatic_promotion":False,
                    "quantities":quantities,"session_loss_budget_usd":config.session_loss_budget_usd,
                    "note":"Same quotes, per-contract costs, news and unchanged session loss budget. Larger quantities can reject otherwise valid signals when planned loss exceeds remaining budget. Replay does not model size-dependent market impact. Exit changes also change later entry availability. Synthetic outcomes are behavior tests; real historical outcomes require future validation."}
        (root/"comparison.json").write_text(json.dumps(comparison,indent=2,allow_nan=False)+"\n")
        print(json.dumps(comparison,indent=2,allow_nan=False))
    elif args.command == "replay":
        config = replace(config, strategy=args.strategy) if args.strategy else config
        try:
            engine = replay(args.ticks, args.calendar, config)
        except ReplayFailure as exc:
            write_run(
                exc.engine,
                args.out,
                {
                    **provenance(args.ticks, args.calendar, config),
                    "failed": True,
                    "error": str(exc),
                },
            )
            raise
        write_run(engine, args.out, provenance(args.ticks, args.calendar, config))
        print(
            json.dumps(
                engine_report(engine, engine.calendar.synthetic),
                indent=2,
                allow_nan=False,
            )
        )
    elif args.command == "research":
        if args.phase == "prepare":
            if not args.ticks or not args.calendar:
                raise ValueError("prepare requires --ticks and --calendar")
            result = prepare(
                str(Path(args.ticks).resolve()),
                str(Path(args.calendar).resolve()),
                config,
                args.out,
            )
        else:
            result = run_phase(args.out, args.phase)
        print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
