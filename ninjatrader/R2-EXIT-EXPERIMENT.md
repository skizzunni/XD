# Compare fixed exits with a trend runner

The supplied records show profitable trades with varied targets and holding
times, including a winning position held about 76 minutes. They do not identify
the entry indicator or its parameters. R2 keeps its declared entry rules and
adds **R2 exit profile=TrendRunner** as a separate paper experiment.

| Exit rule | Fixed | TrendRunner |
| --- | --- | --- |
| Daytime holding limit | 30 minutes | 90 minutes |
| Overnight holding limit | 20 minutes | 45 minutes |
| Profit limit | 1.5R daytime / 1.25R overnight | No fixed profit limit |
| Profit trailing stop | None | Starts after observed 1.5R; follows one initial R behind the best executable quote |
| Paper quantity | Selected 1–10 MNQ | Selected 1–10 MNQ |

Both use the same initial protective stop, cost/risk checks, optional cost-aware
break-even stop, news pauses and pre-close flattening. A trailing stop only
tightens, stays on the tick grid and must remain behind the current executable
quote. Holding a trade across a profile boundary retains its entry profile.
Gaps can still fill worse than the stop and can exceed the loss budget.

Use a fresh **r2-runner-sim101-001** run ID (or another unused ID), select
**R2 exit profile=TrendRunner**, and match that ID in the launcher. Native risk
remains shared across this bot's runs; runner learning is stored separately from
fixed-exit learning. Start at **Paper contracts=2** with the current $100 session
budget. The default native quantity remains one until explicitly selected.
Larger sizes must fit the full planned stop and fees within the remaining
budget; the bot does not silently raise that budget. Partial fills of the same
entry order are aggregated, and partial exits make one closed trade and one
learning outcome. Exiting cancels any unfilled entry remainder; a late entry fill
after an exit has begun triggers a fault and flatten request.
Do not run competing instances on the same account/contract.
The browser shows the exit profile, working stop and refresh time. Every loss
still receives thirty diagnostics. Verify the actual simulated stop order in
NinjaTrader's Orders tab after compilation and the first paper entry.

For an offline comparison on real, identical quoted data, from the project folder:

```powershell
python main.py --config config.full-session-runner-paper.json compare-r2-exits --ticks path\ticks.csv --calendar path\calendar.json --out runs\exit-comparison-001
```

This freezes adaptive entry tightening for both branches so the exit comparison
starts with the same signal filters. Costs, news, quantity and risk settings are
identical. Each branch writes its own trades, loss reviews, profile learning
and provenance; `comparison.json` records both reports and source hashes.
Different exits also change the availability of later entries. Nothing
automatically promotes a historical winner. Use untouched future data to select
an exit policy. The supplied statements alone cannot replay intratrade quotes,
missed signals or alternative fills, so they cannot establish that this variant
would have improved those trades.

The $1.50 fee in this comparison configuration comes from the supplied MNQ cash
and fills records. Spreads/slippage and this simulation's actual fee schedule
still need calibration; **Actual costs calibrated** remains false.

For a **1, 2, 5, 10 contract sweep** with the same configured session budget:

```powershell
python main.py --config config.full-session-runner-paper.json compare-r2-sizing --ticks path\ticks.csv --calendar path\calendar.json --out runs\size-comparison-001
```

Larger quantities can yield fewer fills because the risk reservation no longer
fits. The replay uses the same per-contract fills and does not predict market
impact at higher size. Compare net P&L, actual quantity, fees and drawdown;
increased size alone does not improve the entry signal.
