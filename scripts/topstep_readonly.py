"""Authenticate locally and verify Topstep accounts/MNQ data without orders."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from bot.topstep_gateway import TopstepGateway


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True,
                        help="A new local JSON path; existing files are preserved")
    args = parser.parse_args()
    gateway = TopstepGateway(execution_enabled=False)
    gateway.login_from_environment()
    accounts = gateway.accounts()
    contracts = [item for item in gateway.contracts("MNQ")
                 if item.get("symbolId") == "F.US.MNQ" and item.get("activeContract")]
    bars = []
    if len(contracts) == 1:
        end = datetime.now(timezone.utc)
        bars = gateway.bars(contracts[0]["id"], end-timedelta(hours=2), end, limit=200)
    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "order_writes_enabled": False,
        "accounts": [{key: account.get(key) for key in
                      ("id", "name", "balance", "canTrade", "isVisible")}
                     for account in accounts],
        "active_mnq_contracts": contracts,
        "completed_one_minute_bars_received": len(bars),
    }
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(f"Read-only Topstep check saved to {args.out}; no order endpoint was called")


if __name__ == "__main__":
    main()
