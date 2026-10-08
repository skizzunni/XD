# TradingView automatic MNQ simulation

Use **Strategy Tester** for this version. It trades automatically in TradingView's
broker emulator and displays a dashboard on the chart. No Databento API key,
Python server, NinjaTrader connection, subscription to this bot, webhook or paid
alert is required. Chart data access is a separate TradingView/exchange matter.

TradingView explicitly says Pine strategies cannot place orders in its built-in
Paper Trading account or Trading Panel brokers. Your selected workflow is the
automatic **Strategy Tester** simulation. Do not expect these trades in the
Trading Panel's Paper Trading balance.

## Install on your chart

1. Open TradingView's full chart. Search **MNQZ2026** and select **CME_MINI:
   MNQZ2026**, the December 2026 Micro E-mini Nasdaq contract. Use ordinary
   candles, **5 minutes**, and the **electronic/extended session** (include
   overnight data). Do not use `MNQ1!`, NQ, Heikin Ashi, Renko or a different
   expiry. Set the chart timezone to New York for convenient reading; the
   strategy calculates Eastern time internally.
2. Open **Pine Editor**, create a new strategy, select all of its starter text
   and replace it with the entire content of **MNQ_R2_Paper.pine** from this
   folder. Use the generated `.pine` file, not the `.template.pine` file. Save
   it, then click **Add to chart**. The complete ZIP also includes
   **COPY-TRADINGVIEW.cmd** at its root: after Extract All, double-click it to
   copy the generated script, then press Ctrl+V in Pine Editor. You can instead
   open the `.pine` file in Notepad and copy its text directly.
3. Open the strategy's gear icon → **Inputs**. Start with **Account stage =
   Funded**, **Starting MNQ contracts = 2**, **Maximum MNQ contracts = 10**,
   both adaptive options enabled, **Session loss budget = $100**, and
   break-even enabled at **1R**. Funded has no daily profit target;
   Evaluation applies the configurable **$750** session target. These are
   simulation policies, not funding-firm certifications or broker connections.
4. Set **Paper experiment start (ET)** to the date/time you want to measure.
   The default October 7 includes historical results after that time. Set it
   to your current Eastern date/time for a new forward experiment; bars
   before it still build the volatility history. Record your chosen start
   and settings. Changing settings or the amount of chart history recalculates
   results; this Pine version is not the standalone SQLite journal.
5. In **Properties**, keep **Initial capital = 25,000**, **Commission = $0.75
   cash per contract** (each side), **Slippage = 1 tick**, **Pyramiding = 0**,
   **Recalculate on every tick = on**, **After order is filled = off**, and
   **Fill orders on bar close = off**. The default **1% margin** is only a
   provisional emulator setting, not actual broker/prop-firm margin. If you
   change Properties commission or slippage, also match Inputs **Round-trip
   fees per contract** to twice the per-side commission and **Exit slippage
   ticks** to Properties slippage. Until verified, fees remain provisional.
6. Open the bottom **Strategy Tester / Strategy Report** tab. **List of trades**
   contains simulated entries/exits; the performance summary shows results.
   The dashboard appears at the chart's upper right. No separate dashboard
   launch command is needed. If it overlaps another indicator, hide that
   indicator or maximize this chart.

TradingView's own Pine Editor compile and Add to chart are the final platform
acceptance checks. The cloud checks use an independent Pine runtime; they do
not constitute a successful native TradingView compilation. If Pine Editor
shows an error, retain its full message and line number.

## Tell whether it is working

The dashboard shows the current chart price, chart-bar time, last script
execution clock, position, stop, realized/open P&L, cash ATR20, learned quantity
and **Decision (last close)**. Price and execution clock refresh when TradingView
delivers chart updates. Entry decisions and learning update at completed
five-minute bars. There is no separate clock or data connection when the
chart/script is not executing; leaving a static browser picture open does not
prove a running feed. Quote timestamps/entitlement cannot be inferred precisely
from a five-minute candle.

| Decision | Meaning and action |
| --- | --- |
| `SCANNING: NO_QUALIFYING_SETUP` | History and guards passed; no fresh six-bar pullback breakout qualifies. |
| `ENTRY_PENDING` | A capped limit entry and protective stop have been submitted to the emulator; an unfilled entry expires after the next five-minute bar. |
| `MANAGING_POSITION` | It is managing an open simulated position. |
| `ATR_WARMUP` | It needs 21 complete prior cash sessions, with no missing reviewed cash day. Use the individual December contract with extended-session data; load available history. The script requests up to 5,000 fifteen-minute bars. It never invents missing history. |
| `NEWS_PAUSE` | A supplied scheduled-release window overlaps the entry/fill interval. |
| `OUTSIDE_REVIEWED_ENTRY_HOURS` | Opening warmup, pre-close cutoff, maintenance, weekend, holiday or warmup-only date. |
| `RISK_BUDGET_CANNOT_FIT_ONE_CONTRACT` | Even one contract's initial-stop risk plus modeled costs exceeds the remaining budget. A profitable record does not override this check. |
| `SESSION_RISK_LOCK` | Loss/target protection or a detected fill/cost mismatch paused the session. Keep the record; do not change the start time to erase losses. |
| `BEFORE_EXPERIMENT_START` | The chosen start time has not arrived on chart data. |
| `WRONG_CONTRACT` / `USE_STANDARD_5_MINUTE_CANDLES` | Correct the symbol/chart settings above. |
| `CALENDAR_EXPIRED_OR_MISSING` | This release is dated for October 7–30, 2026. Refresh the reviewed calendar before November. |

Free TradingView MNQ quotes may be delayed. Check the symbol's **Data is delayed**
notice. The input **Chart data entitlement** is a label you set; it neither
detects nor upgrades entitlement. **Delayed research** is the default and accepts
the chart's delayed candles for experimentation. A real-time label is appropriate
only when your TradingView data access actually provides real-time MNQ. Moving
quotes can still be delayed. Real-time results and delayed results should be
kept as separate experiments.

## Entries, exits, learning and losses

R2 scans reviewed electronic sessions, normally Sunday 18:00 to Friday 17:00 ET,
with daily 17:00–18:00 maintenance. It does not force a trade and has no daily
trade-count cap. A valid entry needs six contiguous closed five-minute bars,
momentum of at least 0.05 cash ATR20, a countertrend pullback, a breakout of the
previous two highs/lows, a directional close in the outer 30% of the candle,
and bounded candle width/stop distance. Cash efficiency starts at 0.40 with
stop risk at most 0.20 ATR; overnight starts at 0.55 and 0.10 ATR. The session
opens for entries after 30 minutes, stops new entries 15 minutes before close,
and requests flattening five minutes before close. Supplied news windows pause
entries and close positions. The risk day changes at **18:00 ET**, not midnight.

Cash ATR20 uses the same prior-session true-range definition as the tick bot,
aggregated from 26 complete fifteen-minute cash candles per day. The higher
timeframe request uses `lookahead_off`; the current cash day cannot enter its
own trading day's ATR. Fifteen-minute candles do not prove the tick bot's
complete tick coverage, bid/ask freshness or liquidity. Incomplete/missing cash
sessions reset warmup. This smaller history request is intended to fit ordinary
chart access; actual available history is still controlled by TradingView.

TrendRunner has no fixed profit target. It requests cost-aware break-even after
1R and a monotone one-R trailing stop after 1.5R. Stop changes use completed
bar closes; they do not retroactively use an earlier intrabar high. Maximum
holds are 90 cash minutes / 45 overnight minutes. Initial protective orders
are paired with entry. There is one position, no averaging into losers, and
each submitted quantity reserves full worst-entry stop risk plus modeled fees
and exit slippage. The session budget is containment, not a guaranteed loss ceiling;
gaps and the emulator's path/fill assumptions can exceed it.

Quality learning is separated by long/short and cash/overnight. After eight
eligible completed observations, a nonpositive net result tightens that side's
efficiency requirement by 0.15 (capped at 0.90); positive results restore its
base. Size starts at 2 and can grow one contract per qualifying block of 20
completed, risk-normalized trades, up to 10. Growth requires net ≥4R, profit
factor ≥1.5, at least eight winners, positive net without the largest winner,
drawdown <3R, a positive latest trade and five clean completions since a size
reduction. Consecutive losses, weak windows and fill/cost faults reduce size.
Quantity also fits the unchanged cash budget. See the original
[adaptive policy](../ninjatrader/R2-ADAPTIVE-SIZING.md).

Every losing completed position gets **30 diagnostic questions and answers**.
The chart retains the latest 20 loss reviews. Choose **Loss review page 1–3**
and **Loss to review** in Inputs; each page displays 10 answers at bottom left.
Disable **Show loss review** for more chart space. **Pine Logs** receives all
30 answers per loss when enabled; use Pine Editor's **Pine Logs** command/menu.
Logs are subject to TradingView retention limits. Save your report/settings
outside TradingView for a lasting experiment record. Unknown broker custody,
latency, partial fills, actual spread, causal market explanations and future
generalization are explicitly left unknown. Learning changes only the bounded
filters and sizing; it does not rewrite source code after a loss.

## What this port can measure

Strategy Tester fills orders from its candle/forward-update model. Historical
fills, spread, limit queues, size impact, latency and stop custody differ from
the tick bot and a broker. Entries expire after one five-minute bar here,
rather than five seconds. Protective fills can occur between closed-bar
decisions, but stop improvements occur only at bar close. Neither Bar Magnifier,
Deep Backtesting nor paid alerts are required. The $0.75 per-side fee, one-tick
slippage and 1% margin defaults are provisional assumptions.

The report mixes historical simulation after your chosen start with forward
simulation as bars arrive. Reopening/editing reloads the available history and
can change results; `calc_on_every_tick` supplies live dashboard updates, while
entries and adaptive state commit only on confirmed bars. Save the date range,
settings, fees and data label when comparing experiments. Actual market results
are needed to assess profitability; offline synthetic tests check implementation.

## Developer validation

From the repository root, using its existing virtual environment and Node.js:

```bash
.venv/bin/python scripts/build_tradingview.py
.venv/bin/python -m unittest tests.test_tradingview -v
cd tradingview/validation
npm ci --ignore-scripts --no-audit --no-fund --cache /workspace/.cache/mnq-npm
npm audit signatures --cache /workspace/.cache/mnq-npm
npm run check
```

The locked, signature-verified PineTS dependency is development-only. It
executes extracted production Pine helpers against independent Python outcomes
and the whole source against synthetic bar data. It is unaffiliated with
TradingView and is not the official Pine compiler/emulator. No market-data API
or paid request runs in these checks. The generated calendar retains the
repository's reviewed dates, sources/hash, release windows and 30 loss questions.

Official references:

- [Pine strategies and TradingView Paper Trading limitations](https://www.tradingview.com/pine-script-docs/faq/strategies/)
- [Strategy Tester fill models and settings](https://www.tradingview.com/pine-script-docs/concepts/strategies/)
- [Pine automated broker trading availability](https://www.tradingview.com/support/solutions/43000481026-how-to-autotrade-using-pine-script-strategies/)
