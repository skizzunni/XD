"""Refresh the integrity-checked text source used when a ZIP's .cs was moved."""

import hashlib
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "ninjatrader"
source = (root / "MNQPlanPaper.cs").read_bytes()
(root / "MNQPlanPaper.source.txt").write_bytes(source)
(root / "MNQPlanPaper.source.sha256").write_text(hashlib.sha256(source).hexdigest() + "\n", encoding="ascii")
print("Strategy source backup and SHA256 refreshed.")
