# Compare the R2 baseline with a separate guarded paper experiment

Keep the baseline report. The RTH and ETH screenshots are different experiments:
the electronic session adds candles and setups, changes remaining risk and can
change which later setups fit. Their loaded history ranges also differ. A
profitable RTH recalculation does not establish a profitable full-session system.
Keep a fixed ETH baseline through winning and losing sessions; a recovery does
not erase earlier losses or establish that either new guard improves results.
The experiment below changes planned exposure and cash-open handling; it has
not demonstrated better real-market profitability.

## Add the guarded test

1. Download the complete new ZIP and **Extract All**. Double-click
   **COPY-TRADINGVIEW-GUARDED.cmd**, which copies
   **tradingview/MNQ_R2_Guarded.pine**.
2. In Pine Editor create a **separate strategy**. Click inside the editor,
   press **Ctrl+A**, then **Ctrl+V**. Replace all starter text; the first line
   must be `//@version=6` and there must be only one `strategy()` declaration.
   Save as **MNQ R2 Guarded Paper Test** and **Add to chart**.
3. Select **MNQZ2026**, ordinary **5-minute** candles and **ETH**, the full
   electronic session. Keep the same costs, margin, date range and starting
   account profile as the baseline. Each Pine strategy has an independent
   simulated ledger; their totals are not one combined account.
4. In Inputs use **Paper test variant = Guarded R2**, **Maximum planned loss per
   new position = $25**, **Cash-open buffer before = 5 minutes**, **after = 10
   minutes**, **Funded**, **Session loss budget = $100**, adaptive start **2** /
   maximum **10**, and cost-aware break-even at **1R**. Properties remain $0.75
   per-side commission and one-tick slippage, matched to Inputs. Calendar and
   the rest of the entry/exit/learning policy remain the dated R2 rules.
5. To compare the same historical day, use the same **Paper experiment start**,
   symbol, ETH candles, costs and loaded-history range in both scripts. Choose
   the appropriate script from the Strategy Report's strategy dropdown.
   Hide one script's chart visuals if their dashboard tables overlap; preserve
   both reports/settings. For a forward experiment, first save the historical
   reports, record a new fixed current Eastern start time, and compare later
   sessions without changing it or fitting new parameters to each loss.

## What changed

The **$25 planned-position allowance** includes every submitted contract's
initial-stop risk at the worst permitted limit entry, round-trip fees and exit
slippage. The actual allowance is the smaller of this cap and the remaining
session budget. It keeps the structural stop where the setup specified it;
it reduces quantity or rejects the setup if even one contract does not fit.
Profits do not enlarge the allowance. $25 is a chosen paper risk budget, not a
learned optimal stop. The cap can exclude winning setups too; it can reduce
trade frequency and keep quantity below the adaptive evidence size. It does
not guarantee that an actual/emulator gap loss stays under $25.

The **cash-open buffer** blocks signal/fill windows overlapping **09:25–09:40
ET** with these defaults and requests flattening at the first completed bar
reaching the buffer start. It prevents a new position intentionally being
carried through the 09:30 cash open. Entries resume only after a qualifying
closed bar outside the buffer, so the effective first new signal can be 09:45.
Bar-close execution, quote gaps and the emulator's timestamp conventions still
apply. Outside the buffer, the script scans daytime and overnight reviewed
sessions; there is no daily trade-count cap.

Four selectable variants isolate the effects:

| Paper test variant | Planned-position cap | Cash-open buffer |
| --- | --- | --- |
| Baseline R2 | Original session-budget-only risk fit | Off |
| Trade risk cap only | On | Off |
| Cash open guard only | Original session-budget-only risk fit | On |
| Guarded R2 | On | On |

The ordinary **MNQ_R2_Paper.pine** defaults to Baseline R2 and adds audit
visibility. The guarded file defaults to Guarded R2. The pre-change Pine source
is preserved at **baselines/MNQ_R2_PreGuard.pine**; offline execution checks the
baseline's entire synthetic fill/P&L ledger against that source. This guards
against an unnoticed baseline behavior change while adding the experiment.

The dashboard now shows the variant, planned cap, cash-risk rejection count,
number of opening-buffer bars and last completed trade/its initial plan.
`TRADE_RISK_CAP_CANNOT_FIT_ONE_CONTRACT` means a setup was found but exceeds
the selected planned trade allowance. `CASH_OPEN_BUFFER` means the opening
guard is active. The $100 futures-day budget still resets at **18:00 ET**;
all-run net P&L remains cumulative. Saving a new experiment start to erase
the old day's losses would make the comparison invalid.

## Inspect a completed trade

Keep **Write entry plans and every completed trade to Pine Logs = true**.
Open Pine Editor's **Pine Logs** menu. Each submitted entry has an
`R2_ENTRY_PLAN` line; each completed position has an `R2_TRADE_AUDIT` line,
including winners and break-even trades. Losing trades also retain the 30
question review across three chart pages.

The trade audit records side/regime, quantity, signal/reference, initial/final
stop, planned allowance, original risk, actual entry/exit, net P&L, fees,
emulator MFE/MAE, break-even state, exit reason and learning eligibility.
It also records experiment settings, the symbol, chart session, loaded-history
start and reviewed calendar hash. Timestamps are TradingView-reported bar
timestamps in Eastern, not proof of tick execution times. Save the trade report
and copied logs/settings outside the chart; platform log limits can omit old
records. An incomplete log cannot establish a full-account reconciliation.

For review, provide **List of trades**, the **Pine Logs trade audits**, and the
strategy's Inputs/Properties. Those distinguish a failed price setup, fees
erasing a small winner, a stop/slippage gap, a timed exit, an opening-buffer
exit or a risk-fit rejection. They do not prove why the market moved or measure
real broker queues, latency, spread or partial fills.

Optional local audit analysis, using the user's already-installed Python:

```powershell
python .\scripts\analyze_pine_audit.py --log .\pine-logs.txt --out .\new-trade-review.json
```

Run from the extracted bot folder. Save copied Pine Logs as **plain text** in
`pine-logs.txt`. Choose a new output name: the analyzer preserves existing
reports. It deduplicates identical entries, rejects inconsistent MNQ
price/fee/risk totals, keeps distinct configurations/sessions apart and reports
net P&L, win/loss sizes, profit factor, closed-trade drawdown and exit reasons.
Its closed-trade drawdown differs from TradingView's intrabar maximum drawdown.
No API key, data subscription, orders or source auto-fitting is involved.

## Acceptance and comparison

The updated scripts still need Pine Editor Save/Add to chart acceptance. The
original version compiled and produced reports in the user's screenshots;
the new version's cloud runtime is independent PineTS, not the official Pine
compiler. Free quotes can be delayed; entitlement remains a label and delayed
and real-time experiments should stay separate. The reviewed calendar ends
October 30, 2026; refresh before November.

Compare fixed rules on subsequent untouched sessions, recording both scripts'
net results after costs, average win/loss, total drawdown, trade count, opening
exits and skipped setups. Reducing planned risk is a containment change;
a few historical trades cannot establish a profitable strategy or an optimal
filter. Do not promote this experiment from the same day used to motivate it.
