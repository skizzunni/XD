"""Collect completed MNQ one-minute bars locally; this command cannot order."""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from bot.topstep_collector import CompletedBarCollector
from bot.topstep_gateway import TopstepGateway


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/topstep"))
    parser.add_argument("--poll-seconds", type=int, default=20)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    gateway = TopstepGateway(execution_enabled=False)
    gateway.login_from_environment()
    contracts = [item for item in gateway.contracts("MNQ")
                 if item.get("symbolId") == "F.US.MNQ" and item.get("activeContract")]
    if len(contracts) != 1:
        raise RuntimeError(f"Expected exactly one active MNQ contract, found {len(contracts)}")
    contract = contracts[0]
    safe_name = "".join(character for character in contract["name"]
                        if character.isalnum() or character in "-_")
    path = args.data_dir / f"{safe_name}-one-minute.jsonl"
    collector = CompletedBarCollector(gateway, contract["id"], path)
    if args.once:
        count = collector.capture(datetime.now(timezone.utc))
        print(f"Captured {count} new completed bars in {path}; order writes disabled")
    else:
        print(f"Collecting completed {contract['name']} bars in {path}; Ctrl+C stops; order writes disabled")
        collector.run(args.poll_seconds)


if __name__ == "__main__":
    main()
