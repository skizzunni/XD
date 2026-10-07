# R1 continuous paper scanner

R1 is the new default for Sim101 and the funded/evaluation paper configs. It is
an experimental strategy, with no verified profitable edge. P0 and C1 remain
available as their original single-entry baseline arms.

## When it looks for trades

R1 evaluates every newly closed five-minute bar. New setups can form from
10:00 through 15:45 Eastern, using six consecutive closed RTH bars. There is no
daily trade-count setting or count limit. One contract and one open position
remain the position limits. A consumed, expired or filled setup is never retried;
each subsequent entry needs a newly formed setup after the prior exit.

For each six-bar window:

- The closing change from the first open must exceed 0.05 of lagged ATR20 in
  either direction.
- Trend efficiency is absolute net movement divided by the summed close-to-close
  path, including the first bar's movement. The base minimum is 0.40.
- The penultimate bar must close against that trend, forming a pullback.
- The final bar must close beyond the previous two bars' extremes, within the
  top 30% of its range for longs or bottom 30% for shorts. Its range must be
  positive and at most 0.25 of ATR20.
- The stop sits one tick beyond the pullback bar's opposite extreme. Stop risk
  must remain at most 0.20 of ATR20, satisfy the cost floor, and fit the remaining
  session loss budget after fees and anticipated exit slippage.

A fresh setup expires after five minutes. Entry still uses a capped marketable
limit that expires after five seconds, so a fast breakout can be missed rather
than chased. A 1.5 R target, cost-aware break-even protection, 30-minute maximum
hold and 15:55 ET flatten control the exit. Gaps and slippage can still cause loss.

All timed releases in the supplied calendar create a five-minute-before and
ten-minute-after entry pause. Open R1 positions flatten on the first valid tick
in that pause. Scanning resumes afterward with a fresh setup. Late-news days
can trade outside their release windows when marked enabled; warmup-only,
rollover, early-close and unmapped days remain blocked. P0/C1 retain their
original whole-day late-news exclusion.

## What happens after a losing trade

Every loss records thirty evidence-based diagnostic questions plus entry/exit,
costs, stop behavior, MFE/MAE and market information. A loss alone does not prove
its cause. Data/order faults block further trading until reconciled and count
toward risk; they are excluded from training the entry-quality filter.

R1 maintains the most recent eight eligible completed outcomes separately for
longs and shorts. If their total fee-adjusted result is nonpositive, the minimum
trend efficiency rises by up to 0.15, capped at 0.90. A positive total over the
latest eight outcomes restores the configured base. With fewer than eight
outcomes it uses the base. Only results closed strictly before a new signal
are eligible: future or still-open trades cannot train that signal.

This is a bounded paper adaptation, not a rewrite of the trading algorithm or
evidence that the filter improves performance. `R1 adaptive quality filter=False`
allows a fixed-filter comparison on a separate replay. The existing locked
research family rejects R1; a separate predeclared evaluation is still needed.

## Risk and restarts

The $100 default is a **cumulative session loss budget**, not a per-trade allowance.
New entries reserve stop risk, fees and exit slippage against remaining budget.
Gains do not expand the maximum risk allowed on a new trade. Evaluation uses the
cumulative $750 daily target; Funded has no daily profit target. External prop-firm
rules and other strategies' P&L are not included in this bot's internal ledger.

Native results persist in
`Documents\NinjaTrader 8\MNQPaper\risk-history\<account-instrument>.csv`, across
run IDs. This retains the bot's realized session results and learning history.
Duplicate or damaged rows block activation instead of resetting risk. Keep this
file. Account/instrument ownership permits one current strategy instance; it is
released when that instance terminates. Per-setup `.entry` claims prevent duplicate
orders after restarts while allowing later setups. Do not delete active claims.

Reconcile any open position before updating/restarting. Use a fresh **Paper run ID**
when existing tick logs are nonempty. The launcher asks for the exact run ID
shown in NinjaTrader; the initial value is `r1-sim101-001`. For a later run,
launch `python windows_setup.py dashboard --run-id r1-sim101-002`. Restarting
only the browser dashboard keeps the current run ID.
Only Sim101 and Playback101 are permitted for current-market orders. Native
historical Strategy Analyzer runs do not persist/train the live result history;
use Python replay or Playback101 to compare R1 adaptation chronologically.

## Live dashboard

NinjaTrader publishes current price, open quantity, estimated open P&L, session
realized P&L and blocked/news-pause status about every five seconds of tick time.
The local browser server reads the current CSV snapshot on each request. The
page refreshes every five seconds, showing new state within about 5–10 seconds
under normal load. It generates HTML in memory and does not replace a file in
OneDrive. `START-DASHBOARD.cmd` starts it without reinstalling or restarting the
strategy. The page also opens while waiting for logs, showing run/folder
diagnostics; account and feed values stay unknown until available. The updated
strategy publishes premarket status without enabling premarket trades. Tick age
continues increasing if the feed stops, rather than implying fresh data. Loss
reviews stay expanded across refreshes, and the quality-learning panel shows
observations, recent results and the active threshold. Native fills and commissions
must still be reconciled with Control Center and Trade Performance.

Compile in NinjaTrader with F5 and verify native order/callback behavior on your
PC before enabling. Cloud checks cannot validate NinjaTrader's installed APIs or
prove live simulated profitability.
