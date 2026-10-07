# R2 full-session paper strategy

R2 is a separate paper experiment for MNQ's full CME Globex session. Select
**Strategy arm=R2**. R1 retains its original regular cash-session window;
P0/C1 remain the baseline experiments. A paper account stage labeled Funded
still routes only to Sim101 or Playback101.

## Market hours and session risk

CME specifies Sunday **18:00 ET** through Friday **17:00 ET**, with daily
maintenance **17:00–18:00 ET**. Use Globex hours, not ClearPort or preopen hours.
The included dated CME schedule has no additional intraday halt for the enabled
October dates. Holiday exceptions require a reviewed dated calendar.

R2 checks fresh closed five-minute bars throughout each reviewed open session.
The first setup needs six completed bars after the open; the final accepted
setup ends fifteen minutes before the reviewed close. It flattens five minutes
before that close, normally **16:55 ET**. For an explicit intraday market break,
entries stop five minutes before the break, positions exit one minute before it,
and fresh contiguous bars are required after reopening. It never fabricates a
fill using a maintenance/weekend print or a stale quote.

The futures trading day advances at **18:00 ET**. Overnight and daytime trades
share one cumulative session loss budget. Crossing midnight does not reset that
budget. Restarting with a new run ID restores this bot's recorded risk/learning;
do not delete history or setup claims. Evaluation applies the configured $750
profit target to that futures day; Funded removes that profit target.

## Separate overnight rules

Both profiles require a trend, a closed countertrend pullback and a breakout
using six contiguous completed bars, plus a positive lagged cash ATR20. The
momentum threshold is 0.05 ATR by default, and the breakout closes in the top or
bottom 30% of its range. Stops sit beyond the pullback; entries have a four-tick
chase cap and five-second expiry. Cost and remaining loss-budget checks apply.

| Rule | Daytime 09:30–16:00 ET | Overnight |
| --- | --- | --- |
| Initial minimum trend efficiency | 0.40 | At least 0.55 |
| Maximum stop distance | 0.20 cash ATR20 | 0.10 cash ATR20 |
| Target | 1.5 initial R | 1.25 initial R |
| Maximum holding time | 30 minutes | 20 minutes |
| Observed bid/ask spread | At most 2 ticks | At most 2 ticks |
| Open quantity | Selected 1–10 MNQ | Selected 1–10 MNQ |

R2 requires an observed paired bid/ask at entry; it does not replace missing
quotes with a guessed spread. Native startup waits for the first paired quote.

The native default remains one; select **Paper contracts=2** for the larger
paper test. The table describes **Fixed** exits. The separate
[TrendRunner option and sizing sweep](R2-EXIT-EXPERIMENT.md) retain these entry
guards. Native quality histories are separate by direction, daytime/overnight,
exit profile and selected fixed quantity. Adaptive sizing has a separate
quality profile and learns net R across quantities within its own streams.
An invalid quote after startup, a data gap or an order fault still blocks the
session. Initial connection notifications are distinguished from an actual
interruption after the feed is ready. Real-time CME entitlement remains required.

The profile is frozen at setup/entry. Holding a position across 09:30 or 16:00
does not silently change its original bracket or learning profile. Price gaps
can exceed the planned stop; the simulation records the actual loss.

## News and learning

Entries pause five minutes before until ten minutes after the supplied release,
and an open position is flattened when that pause starts. The full-session
calendar includes 08:30 releases and later statistical releases that were
outside the old RTH calendar. The retained BLS/BEA/Fed snapshots cover their
timed October entries; this is not an exhaustive live news service and does not
model unscheduled announcements.

Every completed loss receives **30 diagnostic questions** with the observed
trade path. R2 records quality-filter learning separately for daytime/overnight
and long/short. After eight eligible completed outcomes in one group, a
nonpositive net tightens its efficiency threshold by 0.15, capped at 0.90; a
positive recent aggregate restores the profile's base. Faulted outcomes count
toward risk but do not train the entry filter. Runtime learning does not rewrite
source. Optional [adaptive paper sizing](R2-ADAPTIVE-SIZING.md) changes quantity
from completed, risk-normalized outcomes and the remaining loss budget; enable
it explicitly. Broader alternatives remain separate paper tests.

## Run and verify

Follow [R2-START-HERE.md](R2-START-HERE.md). The browser refreshes every five
seconds and shows profile, futures day, quote age, open P&L, closed trades,
learning and loss reviews. Keep NinjaTrader, the data connection and dashboard
launcher running, and the PC awake.

The included full-session calendar enables trading dates **October 7–30, 2026**.
Warmup dates are disabled for entries. Refresh official hours/news and export a
new reviewed version before November sessions. A historical Labor Day multi-open
segment is explicitly excluded; the retained final prior-evening segment is
warmup only. Source receipts/hashes and that exclusion are in the calendar JSON.

Python replay accepts real full-session quoted ticks with
`--config config.full-session-paper.json --strategy R2`. It requires the reviewed
full-session calendar and actual complete prior cash history. The standalone
native-rule checker compiles the real contract/session/learning/calendar helpers
without NinjaTrader APIs. Full NinjaTrader compilation and live order callbacks
must still be verified on Windows. Synthetic tests establish behavior, not a
profitable edge.
