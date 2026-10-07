"""Windows setup and a local browser dashboard; never sends trading orders."""

import argparse
import csv
from contextlib import redirect_stdout
from datetime import datetime, timezone
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
import webbrowser


PROJECT = Path(__file__).resolve().parent
SIM_PRESET = PROJECT / "ninjatrader/calendars/mnq-dec26-sim101-2026-10-07-09"


def check_package(calendar_dir=SIM_PRESET):
    required = [
        PROJECT / name
        for name in (
            "requirements.txt",
            "main.py",
            "bot/data.py",
            "bot/dashboard.py",
            "ninjatrader/MNQPlanPaper.cs",
        )
    ] + [Path(calendar_dir) / name for name in ("calendar.json", "MNQCalendar.csv")]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError(
            "Incomplete extracted bot package. Missing files:\n"
            + "\n".join(missing)
            + "\nDownload the complete ZIP, choose Extract All, and run START-SIM101.cmd inside the extracted folder."
        )


def validate_run_id(run_id):
    if not run_id or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
        for c in run_id
    ):
        raise ValueError(
            "Run ID must contain only letters, numbers and hyphens, matching Research > Paper run ID in NinjaTrader"
        )
    return run_id


def discover_runs(home):
    """Report existing ledgers, without treating old logs as running strategies."""
    from bot.dashboard import read_native_csv

    found = []
    for path in (home / "MNQPaper").glob("*/*_events.csv"):
        try:
            rows = read_native_csv(path)
            latest = next((row for row in reversed(rows) if row.get("reason")), {})
            item = {
                "run_id": path.parent.name,
                "account": latest.get("account", "unknown"),
                "strategy": latest.get("arm", "unknown"),
                "timestamp": latest.get("timestamp", ""),
                "reason": latest.get("reason", "No complete events yet"),
                "modified": path.stat().st_mtime,
                "path": str(path),
            }
        except (OSError, ValueError, csv.Error) as exc:
            item = {
                "run_id": path.parent.name,
                "account": "unknown",
                "strategy": "unknown",
                "timestamp": "",
                "reason": f"Cannot read ledger: {exc}",
                "modified": 0,
                "path": str(path),
            }
        found.append(item)
    return sorted(found, key=lambda item: item["modified"], reverse=True)


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


def install(home, calendar_dir=SIM_PRESET, run_id="r1-sim101-001"):
    check_package(calendar_dir)
    validate_run_id(run_id)
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
        f"Research > Paper run ID: {run_id}. Leave historical orders False. Confirm enough individual-contract tick history before enabling."
    )
    print(
        "The launcher does not compile, connect or enable NinjaTrader. Orders on Sim101 are simulated."
    )
    return backups


def dashboard_snapshot(home, run_id):
    from bot.dashboard import native_payload

    validate_run_id(run_id)
    folder = home / "MNQPaper" / run_id
    logs = sorted(folder.glob("*_events.csv"))
    payload = native_payload(logs)
    payload["startup"] = {
        "run_id": folder.name,
        "folder": str(folder),
        "state": "logs_found" if logs else "waiting_for_logs",
        "other_runs": [
            item for item in discover_runs(home)
            if item["run_id"] != folder.name
        ] if not logs else [],
    }
    if not logs:
        payload["note"] = (
            "No strategy logs were found for this run. Account position, P&L and feed status are unknown. "
            "This page does not independently connect to NinjaTrader."
        )
    return payload


def refresh_dashboard(folder, output, home=None):
    from bot.dashboard import render

    payload = dashboard_snapshot(home or folder.parent.parent, folder.name)
    render(payload, output)
    return payload["startup"]["state"] == "logs_found"


def create_dashboard_server(home, run_id, port=8765):
    """Serve only generated snapshots; do not write HTML into OneDrive or expose files."""
    from bot.dashboard import html_document, native_payload

    validate_run_id(run_id)

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if self.path.split("?", 1)[0] != "/":
                self.send_error(404)
                return
            try:
                payload = dashboard_snapshot(home, run_id)
                body = html_document(payload).encode("utf-8")
            except (OSError, ValueError, csv.Error, KeyError, TypeError) as exc:
                payload = native_payload([])
                payload["startup"] = {
                    "run_id": run_id,
                    "folder": str(home / "MNQPaper" / run_id),
                    "state": "read_error",
                    "error": str(exc),
                    "other_runs": [],
                }
                payload["note"] = "Fresh account and feed status are unavailable because the log snapshot could not be read."
                body = html_document(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Browser closed or refreshed before this snapshot finished.

        def log_message(self, format, *args):
            pass

    try:
        return ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE and getattr(exc, "winerror", None) != 10048:
            raise
        return ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)


def watch(home, run_id, once=False, port=8765):
    validate_run_id(run_id)
    folder = home / "MNQPaper" / run_id
    output = folder / "dashboard.html"
    print(
        f"Reading logs: {folder}. Keep this window open; Ctrl+C stops the dashboard server."
    )
    if once:
        refresh_dashboard(folder, output, home)
        webbrowser.open(output.resolve().as_uri())
        return output
    with create_dashboard_server(home, run_id, port) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Browser dashboard: {url}")
        print("Snapshots refresh every 5 seconds. The server reads logs and does not replace dashboard.html.")
        print(f"NinjaTrader: Research > Paper run ID = {run_id}. Open New > NinjaScript Output for startup messages.")
        if not webbrowser.open(url):
            print("Open the Browser dashboard address above in Chrome or Edge.")
        server.serve_forever(poll_interval=0.5)


def launch(home, run_id=None, dashboard_only=False):
    check_package()
    print(f"NinjaTrader user-data folder: {home}")
    runs = discover_runs(home)
    for item in runs:
        print(f"Existing logs: run={item['run_id']}; account={item['account']}; arm={item['strategy']}; latest={item['reason']}")
    if run_id is None:
        ids = {item["run_id"] for item in runs if item["account"] == "Sim101"}
        default = next(iter(ids)) if len(ids) == 1 else "r1-sim101-001"
        print("Use the exact Research > Paper run ID shown in NinjaTrader. Existing logs may belong to stopped runs.")
        try:
            run_id = input(f"Paper run ID [{default}]: ").strip() or default
        except EOFError as exc:
            raise ValueError("An interactive terminal is required, or supply launch --run-id <your-run-id>") from exc
    validate_run_id(run_id)
    print(f"Strategy and dashboard must both use Paper run ID: {run_id}")
    if not dashboard_only:
        install(home, run_id=run_id)
    return watch(home, run_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ninjatrader-home",
        help="Actual NinjaTrader 8 Documents folder, including redirected/OneDrive paths",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-package", help="Check that the downloaded ZIP was fully extracted")
    launcher = commands.add_parser("launch", help="Choose the matching run ID, install, and open the dashboard")
    launcher.add_argument("--run-id", help="Exact Research > Paper run ID; omit to choose interactively")
    launcher.add_argument("--dashboard-only", action="store_true", help="Read existing logs without installing or changing the NinjaTrader strategy")
    install_parser = commands.add_parser(
        "install",
        help="Install source/calendar, preserving different existing files in a backup",
    )
    install_parser.add_argument("--calendar-dir", type=Path, default=SIM_PRESET)
    install_parser.add_argument("--run-id", default="r1-sim101-001")
    dashboard = commands.add_parser(
        "dashboard", help="Watch native CSV logs and open the local dashboard"
    )
    dashboard.add_argument("--run-id", default="r1-sim101-001")
    dashboard.add_argument("--port", type=int, default=8765, help="Local browser port; an occupied port uses a free port")
    dashboard.add_argument(
        "--once", action="store_true", help="Export one standalone HTML snapshot and exit (writes to the run folder)"
    )
    args = parser.parse_args(argv)
    if args.command == "check-package":
        check_package()
        print("Complete extracted bot package verified.")
        return 0
    home = ninja_home(args.ninjatrader_home)
    if args.command == "install":
        install(home, args.calendar_dir, args.run_id)
    elif args.command == "launch":
        launch(home, args.run_id, args.dashboard_only)
    else:
        if not 1 <= args.port <= 65535:
            parser.error("--port must be between 1 and 65535")
        watch(home, args.run_id, args.once, args.port)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Dashboard watcher stopped. NinjaTrader strategy state is unchanged.")
    except Exception as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
