# Adaptive MNQ paper sizing

Two contracts is a starting quantity. This R2 experiment can automatically
increase it toward a selected maximum of ten and reduce it when evidence or
the remaining loss budget calls for a smaller position. It changes quantity
only for a new entry; it never adds to an open or losing position.

Install using [R2-START-HERE.md](R2-START-HERE.md). Disable the old strategy,
reconcile Sim101 positions/orders, run the new **START-SIM101.cmd**, compile
with F5 and use a fresh **r2-adaptive-sim101-001** in both the launcher and
Research → Paper run ID. Keep the real-time CME feed, five-minute ETH chart,
reviewed calendar, history and other R2 guards described there.

| NinjaTrader property | Setting |
| --- | --- |
| Account / Account stage | Sim101 / Funded |
| Strategy arm / R2 exit profile | R2 / TrendRunner |
| Paper contracts (R2 only) | 2: the starting evidence quantity |
| R2 adaptive paper sizing | True |
| R2 maximum paper contracts | 10 |
| Allow historical orders | False |
| Session loss limit | Your explicit paper loss allowance; default $100 |

## Evidence rules

Each fully closed position produces one outcome: net profit after fees divided
by its original total stop risk, called net R. A two-contract and ten-contract
trade with identical per-contract fills produce the same evidence score.
Partial fills cannot count as extra winners. Daytime and overnight streams are
separate, as are Fixed and TrendRunner exits. Long/short outcomes share sizing
evidence within the stream. Adaptive quality learning has its own `_AUTO`
profile, separated from historical fixed-quantity experiments.

At each block of **20 clean completed positions** in that stream, the last 20
can earn **one additional contract** if all these conditions hold:

- Net outcome totals at least **+4R**.
- Positive R divided by negative R is at least **1.5**, or there are no losses.
- At least **8 positions** won after fees.
- The block remains profitable after removing its largest winner.
- The maximum closed-outcome drawdown within the window is below **3R**.
- The latest outcome is positive and at least **5 clean completions** have
  occurred since a sizing reduction.

This is a frozen paper policy, not statistically established confidence. A
strong uninterrupted record can move 2→3 after 20, 3→4 after 40, and toward 10
after 160 qualifying outcomes. It does not promise that those outcomes will
occur. No future, open or same-timestamp closing outcome can influence an entry.

## Automatic reductions

Two consecutive net losses halve the evidence quantity, rounding down with a
floor of one. Additional consecutive losses reduce it by one each. A completed
faulted trade also halves it and cannot help earn a promotion. Once a full
20-position window has nonpositive net or at least 3R drawdown, entering that
weak-performance state halves size once; continued weakness cannot earn growth.
A new reduction restarts the five-completion growth cooldown. A single normal
loss in a profitable record need not reduce size.

For every new entry, actual quantity is the smaller of the evidence quantity
and the number that fits:

```
remaining = session loss limit + min(0, session realized net)
per-contract reservation = original stop points × $2
                           + round-trip fee + reserved exit slippage
```

If only one contract fits, it selects one. If none fits, it rejects that setup.
Profits do not automatically enlarge the session budget. The selected quantity
is frozen before order submission and checked on actual partial fills. Stops,
entry chase limits, news, freshness, full-session coverage, one-position rules
and loss accounting continue to apply. Gaps can exceed a planned loss.

The supplied largest winner had a 41.25-point stop: one contract needs $82.50
before fees, two need $165, ten need $825. With $1.50 fees and a one-tick exit
reserve, a fresh $100 budget fits one, regardless of an evidence quantity of ten.
Adaptive sizing does not silently authorize a larger loss allowance.

## Dashboard and restart

The browser's **Adaptive paper sizing** section shows evidence quantity,
starting/maximum quantity, completed observations, recent net/drawdown in R,
the sizing reason, and the last setup's selected quantity/remaining budget.
SIZING_DECISION and SIZING_UPDATE remain visible in the diagnostic event ledger.
Losses still receive thirty diagnostic questions.

Normalized SIZE rows persist alongside cash RISK and completed LEARNING rows
in the existing six-column risk-history CSV. SIZE and LEARNING rows are excluded
from realized cash restoration. Every adaptive completed position must have
one matching SIZE/LEARNING pair; an incomplete write blocks restart for
reconciliation. Legacy records still restore cash and their original filters,
but cannot supply invented R evidence. A fresh run ID does not erase sizing
history. Keep risk records and setup claims. Changing starting/max settings
recomputes the same saved evidence under those explicit bounds.

## Offline replay

```powershell
python main.py --config config.adaptive-paper.json replay --ticks path\ticks.csv --calendar path\calendar.json --strategy R2 --out runs\adaptive-paper-001
```

Use real, timezone-aware bid/ask ticks and reviewed hours/news, with cash ATR20
warmup. Reports retain actual executed quantities, evidence samples and sizing
decisions. Existing compare-r2-sizing freezes adaptive sizing off to compare
fixed 1/2/5/10 contracts under the same budget. Synthetic checks verify mechanics;
real forward outcomes and native Windows simulation must assess performance.
