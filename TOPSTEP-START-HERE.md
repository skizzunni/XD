# Topstep rebuild: evidence first, then Practice, Combine and XFA

This branch adds the Topstep-specific foundation without enabling order writes.
It uses the official ProjectX REST contract, exact Topstep trading hours and a
cost-aware MNQ risk gate. It does not promise profit and it cannot automate a
Live Funded Account. Topstep's current documentation says the API supports its
simulated environment, including the Trading Combine and Express Funded Account,
but is unavailable after a Live call-up.

## Firm rules captured on October 8, 2026

For a **50K Standard Trading Combine**, Topstep publishes a **$3,000 profit target**,
**$2,000 trailing Maximum Loss Limit**, **5 mini / 50 micro** maximum position
and a 55% best-day consistency target. The trading day runs from **5:00 PM CT to
3:10 PM CT**; the bot stops new entries at **3:08 PM CT** and must be flat by
3:10. The firm maximum is only an outer boundary, never a recommended size.

An XFA starts at a $0 balance. Its MLL begins at -$2,000 for the 50K tier,
trails at end of day and eventually locks at $0. Its current Scaling Plan limit
must be read from Topstep before every session; increases do not become available
mid-session. The bot therefore takes an explicit platform maximum rather than
guessing an XFA size from balance.

Topstep allows automation with conditions, prohibits HFT/SIM-fill exploitation,
maximum size into major news, account stacking and orders outside the best bid
or offer. The API has no sandbox: test against a Practice account. API order flow
must originate from the trader's personal device; a VPS, VPN or remote order
relay is prohibited. Research and read-only analytics may run remotely.

Official references used:

- https://help.topstep.com/en/articles/8284197-trading-combine-parameters
- https://help.topstep.com/en/articles/8284204-what-is-the-maximum-loss-limit
- https://help.topstep.com/en/articles/8284208-consistency-at-topstep
- https://help.topstep.com/en/articles/8284215-express-funded-account-parameters
- https://help.topstep.com/en/articles/8284223-what-is-the-scaling-plan
- https://help.topstep.com/en/articles/8284206-when-and-what-products-can-i-trade
- https://help.topstep.com/en/articles/10305426-prohibited-trading-strategies-at-topstep
- https://help.topstep.com/en/articles/11187768-topstepx-api-access
- https://gateway.docs.projectx.com/docs/intro

Rules can change. Recheck them before activating a new account or promoting a
bot. Topstep's published API access is a separate paid subscription; credentials
belong only in local environment variables and must never enter this repository.

## Local risk policy

The supplied 50K Standard profile begins with **1 MNQ**, can adapt only up to
**10 MNQ**, reserves no more than **$50 per position**, locks after **-$200** or
**+$500** in a Topstep session, preserves a **$500 buffer above the current MLL**, and
locks after three consecutive losses. Quantity includes the structural stop,
the observed **$1.22 MNQ round-turn commission/fees**, and two modeled slippage
ticks. Profits never expand the day's risk allowance. One open position only;
no pyramiding or additions to losers.

These are starting controls for forward research, not fitted optimal values.
`config.topstep-50k-standard.json` keeps `execution_enabled` false. A structural stop
cannot be moved closer merely to fit more contracts. If one MNQ does not fit,
the setup is rejected. Every live submission must have a server-side stop and
target through Auto OCO brackets. A rejected or ambiguous order is reconciled
against open orders and positions and is never blindly retried.

## Strategy specification to test

The candidate remains a transparent trend/pullback system rather than an opaque
profit promise:

- ordinary five-minute MNQ bars, with a fifteen-minute context series;
- lagged ATR20 from complete prior cash sessions;
- six closed bars establish trend efficiency and a pullback breakout;
- separate RTH and overnight thresholds and learning records;
- reviewed high-impact news blackout, cash-open buffer and maintenance cutoff;
- structural stop, minimum 2:1 planned reward/risk after costs, break-even only
  after costs and risk are covered, monotone runner, and time exit;
- no daily trade-count cap, while cash, loss-streak, data, news and MLL locks can
  stop entries.

Charts and completed-position rows cannot reveal the causal entry signal, order
queue, MAE/MFE or whether a discretionary decision was repeatable. Do not change
the entry rules based on one winning day. Import matching Topstep bars, orders,
fills and completed positions before attributing a win or loss to a setup.

## Promotion gates

1. **Offline reconstruction:** reconcile every imported fill, fee and completed
   position. Backtest at least 60 sessions and 150 positions with chronological
   train/validation/holdout partitions. Reject variants that rely on one day,
   one side, one regime or one oversized winner.
2. **Untouched historical holdout:** positive net expectancy after actual costs
   and adverse slippage, profit factor above 1.25, no MLL/DLL violations, and no
   single day contributing more than 40% of total net profit.
3. **Practice forward test:** at least 20 Topstep trading days and 100 completed
   positions with zero orphan orders, duplicate orders, unprotected positions,
   stale-data entries, news-window entries or reconciliation mismatches.
4. **Trading Combine:** manually change the stage only after saving the Practice
   report. Keep the same frozen strategy and lower of learned size, local cash
   fit and Topstep's current platform limit.
5. **Express Funded:** create a new account profile because TC profits do not
   transfer. Read the current Scaling Plan each session. Never carry the TC
   balance, MLL floor or learned risk allowance into the XFA.
6. **Live call-up:** disable all order endpoints. Continue read-only analysis and
   hand execution back to the supported Topstep interface.

Passing these gates reduces avoidable software and risk errors. It does not
establish future profitability.

## Read-only local connection

Topstep says API access must run on your personal device. After arranging access
and generating a key, extract the complete package on that device, run
`SETUP-TOPSTEP.cmd`, then run
`START-TOPSTEP-READONLY.cmd`. The launcher hides key input, keeps it only in the
child process environment, lists eligible accounts, discovers the one active MNQ
contract and requests completed one-minute bars. It never calls an order endpoint.

Do not paste the API key into chat, source, JSON or screenshots. The exact account
must be selected only after reviewing the read-only report. Practice order routing
is intentionally not included in this first activation step.

After the read-only check succeeds, `START-TOPSTEP-COLLECTOR.cmd` records
completed one-minute bars in the ignored local `data\topstep` folder every 20
seconds. It is append-only, validates OHLC/time/volume, deduplicates repeated API
windows, excludes future timestamps and contains no order-writing path. Keep the
window open; Ctrl+C stops it. This provides continuous chart context while your
personal PC is running, subject to Topstep/API availability and session hours.

## Manual trade import

Export completed positions as CSV with ID, size, entry/exit time, entry/exit
price, P&L, commissions, fees and direction. From the extracted folder run:

```powershell
python .\scripts\analyze_topstep_trades.py --positions .\positions.csv --out .\manual-review-001.json
```

The analyzer independently verifies MNQ point value, deducts costs, reports net
profit factor, average win/loss, close-to-close drawdown and holding time, and
refuses to overwrite an existing review. Matching orders/fills and one-minute or
tick bars are still required for chart-level MAE/MFE and entry-quality diagnosis.
