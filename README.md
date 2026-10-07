# MNQ strategy research and paper bot

**R1 continuous intraday scanning** is the default paper strategy requested after
the original plan. It has no daily trade-count cap, keeps one open contract, and
uses a bounded quality filter based on completed trade outcomes. **P0 session-open
momentum** and **C1 fair value gap** preserve the original single-entry plan for
comparison. No real-account order route is implemented. See [the R1 rules and
live dashboard guide](ninjatrader/R1-SCANNER.md).

The Python engine runs on Python 3.12+ with a pinned IANA `tzdata` package for
Windows time zone support. The
NinjaTrader strategy is delivered as source and still requires native Windows
compilation, a real calendar, data, and playback acceptance. Synthetic results
are implementation checks, never evidence of profitable trading.

On Windows, start with [the Sim101 current-market guide](ninjatrader/SIM101-START-HERE.md).
Extract the ZIP and double-click **START-SIM101.cmd**. It installs the dependency,
backs up older strategy/calendar files, installs the current dated calendar, and
opens a local dashboard when NinjaTrader produces logs. You compile with F5,
connect your market-data feed and enable the strategy on Sim101.

## Run locally

```bash
python -m pip install --require-hashes --only-binary=:all: -r requirements.txt
python -m unittest discover -v
python main.py demo --out runs/my-demo
python main.py dashboard --run runs/my-demo/R1 --out runs/dashboard.html
```

Open `runs/dashboard.html` in a browser. It shows trades, equity, drawdown, account
stage, events and 30 diagnostic questions for each loss. It refreshes the local
file every 5 seconds; no web service or account credentials are needed.

## Account stages

**Funded paper is the default:** no daily profit target, a provisional $100 session
loss budget, and break-even protection after 1 initial R of favorable movement.
**Evaluation:** $750 daily profit target with the same loss controls. These labels
do not connect a brokerage account. Loss budgets are containment settings; gaps
and slippage can produce a larger loss. Break-even is estimated net of fees and
exit slippage, and cannot guarantee a non-losing fill.
R1 counts fees and realized results cumulatively across trades, reserves new stop
risk against the remaining budget, and can trade a later fresh setup after a
normal loss. Data/order faults still block entries.

```bash
python main.py accounts set my-evaluation --stage evaluation --loss-limit 100
python main.py accounts set my-funded --stage funded --loss-limit 100
python main.py accounts select my-funded
python main.py --account my-evaluation replay --ticks data/ticks.csv --calendar data/calendar.json --out runs/eval-test
```

`runs/accounts.json` stores local labels only. `config.json` freezes the plan
baseline with no break-even/target overlay. `config.evaluation.json` and
`config.funded-paper.json` contain the requested account overlays. An explicit
`--config` selects that profile unless `--account` is also supplied. Research
defaults to the baseline and forbids silently importing account overlays.

The provisional cost model is $1.00 total round-trip fees + one spread tick + one
slippage tick per side = 5 all-in ticks. Replace it with actual fees and calibrated
execution costs before setting `costs_calibrated: true`. With bid/ask input,
observed spread replaces synthetic spread and is not charged twice. Without
quotes the frozen discrete spread is split with the odd tick on the ask side.

## Strategies and data

P0 freezes `(10:00 close - 09:30 open) / prior-session ATR20` after six closed
5-minute bars. Thresholds are +0.10/-0.10, entry is the first tradable tick at/after
15:30 ET, exit at/after 15:59 ET. Emergency stop is 0.20 ATR rounded outward.
ATR includes completed RTH daily bars, including scheduled early closes; those
early-close dates remain ineligible for entry. A missing daily record forces a
fresh warmup. Roll boundaries never use the old contract's close for the new
contract's true range.

C1 uses three completed consecutive RTH candles, the specified gap/range gates,
positive/negative opening return, the first strict midpoint trade-through within
six bars, an outer-wick stop and a 1.5 R target. Touches do not fill. Tick order
resolves stop/target collisions. C1 uses its own engine state.

Tick CSV header:

```text
timestamp,price,volume,contract,tick_id,bid,ask
```

Use timezone-aware timestamps with UTC offsets, positive integer trade volume,
tick-aligned prices and unique per-contract tick IDs. Bid and ask are optional as
a pair. A file of OHLC bars cannot establish tick fills. Duplicates, bad times,
unresolved contracts and gaps fail or block execution with reason codes. Import
deduplication uses SQLite on disk. Never fabricate an exit using a stale quote.

Calendar JSON requires `version: 1`, `source`, `news_as_of`, and explicit `sessions`.
Each row supplies date, timezone-aware open/close, actual contract, `roll_day`, and
`news: {flags: [], releases: []}`. Release rows include `name`, `timestamp`, and
`revision` history. See `ninjatrader/calendar.example.json` for the schema; its
example is intentionally synthetic and is blocked by the native strategy.
Confirm the exchange calendar and macro calendar; do not infer holidays from
weekdays. Keep earlier-release days but flag them; exclude 15:25–16:00 releases.
Provide at least 20 completed prior sessions plus the selected trading sessions.
Optional boolean `trade_enabled` defaults to true; false marks dates as ATR
warmup only. [Ready dated calendars](ninjatrader/calendars/README.md) cover the
requested October 5–6 playback and October 7–9 Sim101 forward test, with cited
official schedule snapshots. Those snapshots do not provide tick prices or
complete historical news revision records.

```bash
python main.py --config config.json replay --ticks data/ticks.csv --calendar data/calendar.json --out runs/baseline
python main.py --config config.funded-paper.json replay --strategy P0 --ticks data/ticks.csv --calendar data/calendar.json --out runs/funded-p0
python main.py --config config.funded-paper.json replay --strategy R1 --ticks data/ticks.csv --calendar data/calendar.json --out runs/funded
python main.py review --run runs/funded
```

Each new output directory receives code/config/data hashes, event ledger, fills,
trade path audits, open-position status, cost stress, loss reviews and a paper
experiment queue. Failed runs retain logs and are labeled unsuitable for
performance. Existing output directories are never overwritten.

## Loss review and improvement

Every loss gets 30 diagnostics. Recorded facts and unknowns stay distinct:
especially broker-server stop custody, external account rules and latency.
Pre-entry market diagnostics use only bars available at entry. Loss reviews can
use the subsequently observed trade path. The native strategy writes an actual
order ledger and a separate 30-question loss-review CSV.

The learning queue groups recurring flags and proposes frozen threshold-grid,
break-even-overlay, and C1 comparisons. It requires at least 30 observations to
recommend experiments. It never changes active trading rules from one loss.
R1 additionally records a fixed adaptation policy: eight eligible completed
outcomes per direction can tighten the trend-efficiency filter, without changing
quantity or risk limits. Faulted outcomes are excluded from quality training.
This paper adaptation is logged and unvalidated; automatic strategy promotion
and unconstrained strategy generation remain absent.

## Locked research

```bash
python main.py --config config.json research prepare --ticks data/ticks.csv --calendar data/calendar.json --out runs/research-v1
python main.py research development --out runs/research-v1
python main.py research validation --out runs/research-v1
python main.py research holdout --out runs/research-v1
```

Prepare rejects synthetic data and uncalibrated costs. It fixes 50%/25%/25%
chronological eligible sessions after warmup. Actual validation/holdout fills
must each reach 150; longer input is necessary when signals skip days. Nine
unique P0 threshold pairs include P0 itself. Selection uses development net
expectancy then drawdown and CSCV/PBO. Validation must pass absolute gates before
holdout is touched. The exclusive holdout-consumption lock is written before
execution, including crashes. Code/config/raw data changes invalidate the version.

Reports include stationary session-block bootstrap, costs ±1 tick per side,
concentration, quarters, drawdown, and C1 relative reject/fault rates. C1 has only
one frozen configuration, so its PBO is not estimable and its automatic promotion
is conservatively blocked until a predeclared C1 family provides selection-risk
evidence. Threshold-shift sensitivity, approved C1 ablations, native/Python daily
reconciliation and a completed 30-session forward-sim gate remain separate
research work; none has passed on actual data yet. Never retune a failed holdout.

## NinjaTrader

Follow [the Windows setup and dashboard guide](ninjatrader/SETUP.md).
Import `MNQPlanPaper`, choose **Arm=R1** and **Account stage=Funded**, and use
`Sim101` or `Playback101`. Keep C1 as a separate replay comparison. Funded/evaluation
classification applies separately to each strategy instance's selected account.
Live/account API adapters are absent. A two-day screenshot does not establish
outperformance; compare raw fills, fees, quantity, sample length and drawdown.
