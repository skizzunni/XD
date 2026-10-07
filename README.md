# MNQ strategy research and paper bot

**R2 full-session scanning** is the Windows Sim101 default. It scans reviewed
CME sessions overnight and during the day, with separate rules and bounded
quality learning for each profile. It has no daily trade-count cap and keeps
one open position, with selectable 1–10 paper contracts. Start with [the R2 install guide](ninjatrader/R2-START-HERE.md)
and [the full rules](ninjatrader/R2-FULL-SESSION.md). **R1** retains its cash-session
experiment; **P0 session-open momentum** and **C1 fair value gap** preserve the
original single-entry plan for comparison. Native routing accepts simulated
accounts only.

The [adaptive sizing experiment](ninjatrader/R2-ADAPTIVE-SIZING.md) starts at
**2 contracts** and can grow toward **10** from completed, risk-normalized
results, with automatic reductions after loss streaks and weak performance.
Set **R2 adaptive paper sizing=True**, **R2 maximum paper contracts=10** and
**R2 exit profile=TrendRunner**. Every setup still fits the explicit remaining
loss budget, so it may use fewer contracts. The
[fixed exit/size comparison](ninjatrader/R2-EXIT-EXPERIMENT.md) remains available.

The Python engine runs on Python 3.12+ with a pinned IANA `tzdata` package for
Windows time zone support. The
NinjaTrader strategy is delivered as source and still requires native Windows
compilation, a real calendar, data, and playback acceptance. Synthetic results
are implementation checks, never evidence of profitable trading.

On Windows, start with [the Sim101 current-market guide](ninjatrader/R2-START-HERE.md).
Extract the ZIP and double-click **START-SIM101.cmd**. It installs the dependency,
backs up older strategy/calendar files, installs the current dated calendar, and
asks for the exact Paper run ID, and opens a local browser dashboard even while
waiting for logs. Missing `.cs` source can be recovered from the included
SHA256-checked text backup. It installs `MNQCalendar-R2.csv` for the R2 settings;
saved legacy calendar paths can resolve to this verified installed calendar.
Follow [the startup repair steps](ninjatrader/FIX-R2-STARTUP.md) for the October 7
calendar error or incomplete-package message. If the strategy is already running,
use **START-DASHBOARD.cmd** to view its logs without reinstalling or restarting it.
That dashboard needs neither source/calendar files nor a dependency installation.
Strategy-installation errors also open the browser with an error notice. Browser pages are
generated in memory, avoiding OneDrive HTML replacement errors. You compile with F5,
connect your market-data feed and enable the strategy on Sim101.

After editing the native source, run `python scripts/refresh_source_backup.py`
to keep its packaged recovery copy and checksum current.

## Run locally

```bash
python -m pip install --require-hashes --only-binary=:all: -r requirements.txt
python -m unittest discover -v
python main.py demo --out runs/my-demo
python main.py dashboard --run runs/my-demo/R1 --out runs/dashboard.html
```

Open `runs/dashboard.html` in a browser. It shows trades, equity, drawdown, account
stage, events and 30 diagnostic questions for each loss. It refreshes the local
file every 5 seconds; this offline export needs no web service or account
credentials. Regenerate the export when its underlying replay files change.
The live native dashboard instead runs a small Python browser server on your PC.
Its entry diagnostics retain recorded blocking checks even after many routine
tick updates. The ledger filters out `LIVE_STATUS` by default, and the browser
can download the complete non-status diagnostic report. Neither feature resets
a trading guard or independently reads the brokerage account.

## Account stages

**Funded paper is the default:** no daily profit target, a provisional $100 session
loss budget, and break-even protection after 1 initial R of favorable movement.
**Evaluation:** $750 daily profit target with the same loss controls. These labels
do not connect a brokerage account. Loss budgets are containment settings; gaps
and slippage can produce a larger loss. Break-even is estimated net of fees and
exit slippage, and cannot guarantee a non-losing fill.
R1/R2 count fees and realized results cumulatively across trades, reserve new stop
risk against the remaining budget, and can trade a later fresh setup after a
normal loss. R2's futures session advances at 18:00 ET, so midnight does not reset
the budget. Data/order faults still block entries.

```bash
python main.py accounts set my-evaluation --stage evaluation --loss-limit 100
python main.py accounts set my-funded --stage funded --loss-limit 100
python main.py accounts select my-funded
python main.py --account my-evaluation replay --ticks data/ticks.csv --calendar data/calendar.json --out runs/eval-test
```

`runs/accounts.json` stores local labels only. `config.json` freezes the plan
baseline with no break-even/target overlay. `config.evaluation.json` and
`config.funded-paper.json` contain the requested account overlays.
`config.full-session-paper.json` selects the R2 full-session experiment; the
`config.adaptive-paper.json` selects the new adaptive two-to-ten runner profile;
older Python CLI default remains R1 for reproducible comparisons. An explicit
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

Follow [the R2 Windows setup and dashboard guide](ninjatrader/R2-START-HERE.md).
Import `MNQPlanPaper`, choose **Arm=R2** and **Account stage=Funded**, and use
`Sim101` or `Playback101`. Keep C1 as a separate replay comparison. Funded/evaluation
classification applies separately to each strategy instance's selected account.
Live/account API adapters are absent. A two-day screenshot does not establish
outperformance; compare raw fills, fees, quantity, sample length and drawdown.
