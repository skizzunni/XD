# Standalone MNQ paper bot with Databento

This version runs the R2 strategy in Python and displays its trades in your
browser. NinjaTrader, its chart, Sim101, and a NinjaTrader Paper run ID are not
required. The account named `paper` is a local simulated ledger. Databento
supplies market data; this program sends no brokerage orders.

## First, verify the program without paid data

1. Download the **complete ZIP**, right-click it and choose **Extract All**.
   Open the extracted folder containing this guide and `START-DEMO.cmd`.
2. Disable the old MNQPlanPaper strategy in NinjaTrader and reconcile any old
   simulated position separately. Its records stay in NinjaTrader.
3. Double-click **START-DEMO.cmd**. Keep the window open. A browser dashboard
   opens automatically. This uses your installed Python; Python 3.12+ is
   required, and dependencies are installed with pinned hashes.
4. Watch **Feed and recorder → Received trade ticks** increase. Within a few
   seconds, trades and loss reviews appear. Each recorded loss has 30 questions.
   The fixture runs four accelerated synthetic futures sessions and then shows
   `DEMO_COMPLETE`. Its prices and P&L cannot establish profitability.
5. Press **Ctrl+C** in the window when finished. The separate demo journal stays
   in `runs\standalone\demo-paper-MNQZ6`. It cannot train the real-data account.

## Then enable actual current-market data

1. Create your own [Databento account](https://databento.com/) and API key.
   Review the provider's current subscription, historical usage and exchange
   licensing charges. Arrange **real-time CME access to `GLBX.MDP3`**, including
   **`tbbo`** for live trade/bid/ask records and **`ohlcv-1m`** for historical
   cash-session bars. Confirm licensing for your intended usage directly with
   Databento. A delayed trial or historical-only entitlement is insufficient.
2. Double-click **START-PAPER.cmd** in the extracted folder. It creates
   `.live-venv` and installs the pinned Databento SDK. Windows Python 3.14 x64
   wheel compatibility was checked; actual Windows launcher execution still
   needs this local acceptance step.
3. Enter your chosen **historical download cap in dollars**. `0` uses cached
   bars only when their estimated retrieval cost is positive. The first run
   needs 21 completed prior cash sessions, so an empty cache usually needs a
   positive cap. Before each missing-session download, the bot checks
   Databento's cost estimate against the remaining launch cap. If the cap is
   exhausted, it stops with an explanation; already verified cached bars remain
   reusable. This cap covers estimated historical requests, **not** live data,
   monthly subscriptions or exchange charges. It is not a billing guarantee.
4. At **Paper account stage**, press Enter for **funded**, as requested.
   `evaluation` applies a $750 futures-day profit target. Funded has no daily
   profit target. Both preserve the configured loss budget.
5. The dashboard opens and the terminal asks **Databento API key**. Paste it
   there and press Enter. Characters are hidden. Never paste your key into
   chat. The bot does not save the key in its journal or dashboard; it also
   accepts a locally configured `DATABENTO_API_KEY` environment variable.
6. Keep the terminal open and your computer awake. The local browser server
   normally uses port **8766** and prints its actual address. It opens on your
   computer; no upload, NinjaTrader connection, or cloud preview is involved.

## How to tell that it is working

The page refreshes every five seconds without replacing an HTML file. A single
dashboard refresh is not proof of a healthy market feed. Check all of these:

| Dashboard item | Expected behavior |
| --- | --- |
| Feed state | `HISTORY`, then `CONNECTING`, then `CONNECTED` |
| Received trade ticks | Increases during active trading, even when entries are paused |
| Last received / Latest tick | Advances with actual Databento market records |
| ATR20 / completed ATR sessions | Positive ATR and `20/20`, derived from 21 complete prior cash sessions |
| Closed entry bars | Six complete fresh five-minute bars before a setup can qualify |
| Entry diagnostics / event ledger | Explains warmup, quality rejections, news, risk, or feed faults |
| Trades / losses / sizing | Updates after completed paper positions; losses include 30 diagnostic questions |

Starting partway through a bar discards the fragment: initial setup evaluation
usually needs **30–35 minutes of fresh ticks** after history loads. No trade is
required for the received counter or dashboard to update. A qualifying setup
is still required to enter; the bot does not manufacture trades to meet a quota.

R2 scans reviewed CME hours, normally **Sunday 6 PM ET through Friday 5 PM ET**,
with daily **5–6 PM ET maintenance**, news pauses and calendar exceptions.
It stops new setups before close and attempts to flatten on actual pre-close
quotes. This is the available futures session, not uninterrupted 24-hour trading.
The loss/risk day changes at **6 PM ET**, not midnight.

## Strategy, costs and account settings

The default `config.adaptive-paper.json` uses **R2 / TrendRunner**, Funded paper,
one position, **starting 2 MNQ / maximum 10**, a provisional **$100 loss budget**,
cost-aware break-even protection and provisional **$1.50 round-trip fees per
contract**. Enter your actual costs in a copied configuration before drawing
performance conclusions. Position size can increase only from completed
profile-specific results; losses/faults can reduce it, and each entry must fit
the remaining cash budget. There is no daily trade-count cap and no adding to
losing positions. See [the sizing policy](ninjatrader/R2-ADAPTIVE-SIZING.md) and
[the R2 rules](ninjatrader/R2-FULL-SESSION.md); their NinjaTrader setup sections
apply only to the older native implementation.

For separate paper account profiles, open a terminal in the extracted folder:

```bat
START-PAPER.cmd --account eval1 --stage evaluation
START-PAPER.cmd --account funded1 --stage funded
START-PAPER.cmd --account experiment1 --config my-paper-config.json
```

`--stage` overrides the launcher's stage prompt. A journal freezes its account,
configuration, calendar and engine version. Use the same label/configuration
to resume it. A different experiment needs a separate label; changing settings
does not silently overwrite an existing record or bypass its loss limit.
These labels do not represent or connect actual funded brokerage accounts.

## Loss learning, persistence and interruptions

Every loss records 30 evidence-based diagnostic answers. Unknown causes remain
unknown. The existing bounded policy adjusts trend-quality thresholds from
eligible completed outcomes separately for daytime/overnight and long/short.
Adaptive sizing uses completed net-R outcomes by exit/session profile. Faulted
trades count toward cash risk and reduce sizing evidence but cannot earn growth.
The program does not rewrite its source or claim to discover a better strategy
after one loss. Broader strategy changes require separate future-data tests.

Unique input ticks are committed to a checksummed SQLite journal before paper
processing, with bounded 100 ms buffering. Restart reconstructs positions,
cash, losses, quality and sizing from committed inputs. A second process cannot
own the same journal. Keep the whole `runs\standalone` folder and back it up
while stopped; SQLite WAL sidecars may contain committed records. The bot
does not import your old NinjaTrader results into the new account.

Disconnects, quotes older than 90 seconds and material data gaps pause entries.
Healthy reconnection needs another six complete fresh bars; restarting cannot
reset cash limits. An unresolved simulated position is preserved during
silence and reconciled at a newly validated executable quote, which can incur
a loss. No exit is fabricated while offline. Fatal mapping, corrupted-journal,
duplicate or disk errors require inspection and restart after repair. Once a
data fault affects a futures day, its later outcomes cannot earn quality/sizing
promotions for that day.

Daily history refresh retries unavailable history every ten minutes, within the
remaining launch cost cap. It runs before the next futures-day open where the verified
cash-minute history is available and fits the remaining launch cap. Missing
history blocks entries. Initial history requires every reviewed cash minute;
missing or thin contract history is not filled with invented bars. Saved tick
journals grow with capture time, and replay on restart can take longer after
many sessions. Do not delete records to reset losses or claim a clean result.

The included reviewed calendar enables **October 7–30, 2026** only. It contains
exchange/news source receipts but is not an exhaustive live news service.
Refresh the reviewed calendar and plan a separate documented experiment or
migration before trading a later month or another contract.

## If setup stops

- **Python not found:** install Python 3.12+ x64 from python.org with PATH
  enabled, reopen the terminal, then run the launcher again.
- **Incomplete package:** Extract All the complete ZIP; both launchers must
  be next to `standalone_paper.py`, `bot`, and the requirements files.
- **HISTORY / download cap error:** check the displayed estimate, provider
  historical permission, cache and your chosen cap. The error does not remove
  verified downloaded history. No cloud API key or paid download was used to
  validate this package.
- **ERROR / provider message:** repair API credentials, licensing, dataset or
  schema permission in Databento, then restart with the same paper account.
  Credentials are redacted from provider error text.
- **STALE_FEED / CLOCK_SKEW:** confirm real-time access, Internet connection and
  Windows automatic clock synchronization. The limit is deliberately retained.
- **Different strategy/config/calendar:** preserve the old record and start a
  clearly named separate experiment; do not delete or overwrite its journal.
- **Dashboard tab closed:** open the address printed by the terminal. If the
  window is closed, rerun the same launcher/settings. Use **Download
  diagnostics** to share a status snapshot without your API key.

Real-data connectivity and strategy profitability are unverified until you
activate your own feed and observe its results. Fills are deterministic paper
estimates on quoted data; latency, size impact, real order queues, partial fills
and broker-server stop custody are not modeled. Stops execute in this local
paper process. Larger quantity multiplies losses as well as wins. Keep these
limits in mind when comparing paper results with a brokerage statement.
