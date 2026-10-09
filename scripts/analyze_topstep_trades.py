"""Create a new cost-aware report from a Topstep completed-position CSV."""

import argparse
import hashlib
import json
from pathlib import Path

from bot.manual_trades import read_topstep_positions, summarize_manual_trades


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--positions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    raw = args.positions.read_bytes()
    trades = read_topstep_positions(args.positions)
    result = {
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "summary": summarize_manual_trades(trades),
        "limitations": [
            "Completed positions do not contain the pre-entry chart context or MAE/MFE.",
            "A chart diagnosis requires matching bars plus order/fill timestamps.",
            "Closed-trade drawdown can understate intratrade drawdown.",
        ],
    }
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(f"Analyzed {len(trades)} positions into {args.out}")


if __name__ == "__main__":
    main()
