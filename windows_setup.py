"""Windows setup and local-file dashboard; never sends trading orders."""

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid
import webbrowser


PROJECT = Path(__file__).resolve().parent
SIM_PRESET = PROJECT / "ninjatrader/calendars/mnq-dec26-sim101-2026-10-07-09"


def documents_folder():
    if sys.platform != "win32":
        raise ValueError(
            "Use Windows, or supply --ninjatrader-home for an offline installation check"
        )
    import ctypes

    buffer = ctypes.create_unicode_buffer(32768)
    result = ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer)
    if result != 0 or not buffer.value:
        raise OSError(
            "Cannot locate Documents. Supply --ninjatrader-home with your actual NinjaTrader 8 folder"
        )
    return Path(buffer.value)


def ninja_home(value):
    return (
        Path(value).expanduser().resolve()
        if value
        else documents_folder() / "NinjaTrader 8"
    )


def install(home, calendar_dir=SIM_PRESET):
    from bot.data import Calendar, checksum

    strategies = home / "bin/Custom/Strategies"
    if not strategies.is_dir():
        raise ValueError(
            f"NinjaTrader Strategies folder missing: {strategies}. Open NinjaTrader 8 once, or supply --ninjatrader-home."
        )
    calendar_dir = Path(calendar_dir)
    calendar = Calendar(calendar_dir / "calendar.json")
    calendar_source = calendar_dir / "MNQCalendar.csv"
    first_line = calendar_source.read_text().splitlines()[0]
    if (
        calendar.synthetic
        or "synthetic=false" not in first_line
        or checksum(calendar_dir / "calendar.json") not in first_line
    ):
        raise ValueError(
            "The preset must be a real frozen calendar with its matching CSV export"
        )
    from main import main as bot_main

    with tempfile.TemporaryDirectory() as directory:
        expected = Path(directory) / "calendar.csv"
        with redirect_stdout(io.StringIO()):
            bot_main(
                [
                    "export-nt-calendar",
                    "--calendar",
                    str(calendar_dir / "calendar.json"),
                    "--out",
                    str(expected),
                ]
            )
        if calendar_source.read_bytes() != expected.read_bytes():
            raise ValueError(
                "Calendar CSV differs from its frozen JSON export; reinstall an intact package"
            )
    active = sorted(s.day for s in calendar.sessions.values() if s.trade_enabled)
    if not active:
        raise ValueError("Calendar contains no enabled trading dates")
    stamp = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    backups = home / "MNQPaper/install-backups" / stamp
    updates = (
        (PROJECT / "ninjatrader/MNQPlanPaper.cs", strategies / "MNQPlanPaper.cs"),
        (calendar_source, home / "MNQCalendar.csv"),
    )
    for source, destination in updates:
        content = source.read_bytes()
        if destination.exists():
            if destination.read_bytes() == content:
                print(f"Already current: {destination}")
                continue
            backups.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backups / destination.name)
            print(f"Previous file preserved: {backups / destination.name}")
        temporary = destination.with_name(
            destination.name + "." + uuid.uuid4().hex + ".tmp"
        )
        try:
            temporary.write_bytes(content)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        print(f"Installed: {destination}")
    print(
        f"Calendar allows {active[0]} through {active[-1]}; earlier dates only build ATR history."
    )
    print(
        "In NinjaTrader: compile with F5; connect your live market-data feed; use MNQ 12-26, 5 Minute, Sim101, R1, Funded."
    )
    print(
        "Paper run ID: r1-sim101-001. Leave historical orders False. Confirm enough individual-contract tick history before enabling."
    )
    print(
        "The launcher does not compile, connect or enable NinjaTrader. Orders on Sim101 are simulated."
    )
    return backups


def refresh_dashboard(folder, output):
    from bot.dashboard import native_payload, render

    logs = sorted(folder.glob("*_events.csv"))
    if not logs:
        return False
    render(native_payload(logs), output)
    return True


def watch(home, run_id, once=False):
    if not run_id or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
        for c in run_id
    ):
        raise ValueError(
            "Run ID must contain only letters, numbers and hyphens, matching the strategy setting"
        )
    folder = home / "MNQPaper" / run_id
    output = folder / "dashboard.html"
    opened = False
    waiting_printed = False
    print(
        f"Watching: {folder}. Keep this window open; Ctrl+C stops the dashboard watcher."
    )
    while True:
        try:
            ready = refresh_dashboard(folder, output)
            if ready and not opened:
                webbrowser.open(output.resolve().as_uri())
                opened = True
                print(
                    f"Dashboard opened: {output}. Data updates every 5 seconds; the page refreshes every 5 seconds."
                )
            elif not ready and not waiting_printed:
                print(
                    "Waiting for strategy logs. Compile, configure and enable MNQPlanPaper in NinjaTrader with the matching Paper run ID."
                )
                waiting_printed = True
        except (OSError, ValueError) as exc:
            if once:
                raise
            print(f"Dashboard refresh failed; retrying: {exc}", file=sys.stderr)
        if once:
            return output if opened else None
        time.sleep(5)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ninjatrader-home",
        help="Actual NinjaTrader 8 Documents folder, including redirected/OneDrive paths",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    install_parser = commands.add_parser(
        "install",
        help="Install source/calendar, preserving different existing files in a backup",
    )
    install_parser.add_argument("--calendar-dir", type=Path, default=SIM_PRESET)
    dashboard = commands.add_parser(
        "dashboard", help="Watch native CSV logs and open the local dashboard"
    )
    dashboard.add_argument("--run-id", default="r1-sim101-001")
    dashboard.add_argument(
        "--once", action="store_true", help="Render one snapshot and exit"
    )
    args = parser.parse_args(argv)
    home = ninja_home(args.ninjatrader_home)
    if args.command == "install":
        install(home, args.calendar_dir)
    else:
        watch(home, args.run_id, args.once)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Dashboard watcher stopped. NinjaTrader strategy state is unchanged.")
    except Exception as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
