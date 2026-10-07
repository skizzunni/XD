# NinjaTrader 8 installation and dashboard

The cloud runner cannot run NinjaTrader's Windows UI or compile against your
installed NinjaTrader assemblies. C# syntax is checked in cloud; native compile,
order lifecycle, connection handling, and playback tests are still required.
The strategy is not yet deployed to your PC.

For **Sim101 on current market data**, follow [SIM101-START-HERE.md](SIM101-START-HERE.md)
and double-click `START-SIM101.cmd` from the extracted project. The launcher
locates its own files and installs the dated October 7–9 calendar. It preserves
older files in backups and opens the dashboard when logs exist. Compilation and
enabling still happen in NinjaTrader.

1. Download and unzip the delivered `mnq-paper-bot.zip` on your Windows PC.
   Install Python 3.12+ for the dashboard and open NinjaTrader 8 once.
   In PowerShell inside the unzipped project run
   `python -m pip install --require-hashes --only-binary=:all: -r requirements.txt`.
   The pinned `tzdata` package supplies Eastern/DST rules on Windows.
2. In NinjaTrader choose **New → NinjaScript Editor**. Copy
   `MNQPlanPaper.cs` into `Documents\NinjaTrader 8\bin\Custom\Strategies\`.
   Alternatively run the bundled `Install-PaperBot.ps1`. Press **F5** to compile.
   If NinjaTrader reports compiler errors, send the complete errors with line
   numbers. C# syntax checks do not prove native API compatibility.
3. Supply a **verified frozen session/news/roll calendar**, including at least 20
   completed prior RTH days plus the sessions to trade. The exact `contract`
   string must match NinjaTrader's instrument, such as `MNQ 12-26`. Use actual
   individual-contract history, not an unexamined back-adjusted continuous series.
   The included example is a schema, not a real calendar; native activation rejects
   synthetic calendars. Convert your verified JSON on the PC or in this cloud:

   For the requested **MNQ December 2026, October 5–6 playback**, a ready CSV is
   included at `calendars\mnq-dec26-2026-10-05-06\MNQCalendar.csv`. Copy it to the
   location below after installing and compiling the updated strategy; you do
   not need to author or export that file yourself. Earlier included dates are
   warmup only. See [calendar sources and scope](calendars/README.md).

   ```powershell
   python main.py export-nt-calendar --calendar data\calendar.json --out MNQCalendar.csv
   ```

   Put that CSV in `Documents\NinjaTrader 8\MNQCalendar.csv`, or set **Frozen
   calendar CSV** to its full path. An absent date or unresolved contract blocks
   entries. Empty news lists mean your supplied calendar has no release; verify
   completeness rather than treating an empty example as authoritative.
4. Connect **Playback** with actual MNQ market replay data for an initial test.
   Open an MNQ **5 Minute** chart, with a trading-hours template that includes all
   RTH bars. Load enough completed history for ATR20 (normally at least 30 trading
   sessions, allowing startup and holidays). Verify availability of the added
   one-tick history; five-minute bars alone are insufficient. Set NinjaTrader's
   display time zone to Eastern; the strategy's **Platform display time zone ID**
   must be `Eastern Standard Time`. Windows applies daylight saving automatically.
5. Right-click the chart → **Strategies** → add **MNQPlanPaper**:

   | Setting | Initial paper setting |
   | --- | --- |
   | Account | `Playback101` for playback; `Sim101` for feed-connected paper |
   | Strategy arm | `P0` |
   | Account stage | `Funded` |
   | Session loss limit | `$100` provisional; replace with your actual allowed risk |
   | Use break-even protection | `True` for requested account profile |
   | Evaluation daily profit target | `$750`; used only for `Evaluation` stage |
   | Round-trip fees / spread / slippage | Replace provisional values with actual charges/fill calibration |
   | Allow historical orders | `False` for forward/playback simulation |
   | Paper run ID | A new readable ID, e.g. `p0-playback-001` |

   Orders use the added tick series. Entries are marketable capped limits and
   expire after five seconds without chasing. Native limit-fill slippage is not
   artificially added to account fills; compare actual quotes/fills to the frozen
   cost stress schedule before interpreting performance. The baseline research comparison uses
   **Use break-even protection=False**, funded stage and 0.10/0.10 thresholds.
   Do not mix your friend's strategy or manual trades into this account/instrument
   while testing. An existing position blocks startup; it is not blindly flattened.
6. Enable only after successful compilation and calendar/history verification.
   Read the NinjaScript Output and Control Center Log. `ATR_WARMUP` means more
   valid prior sessions are needed. `UNRESOLVED_SESSION`, `UNRESOLVED_CONTRACT`,
   `DATA_GAP`, or `STALE_FEED` means no new entry until the cause is resolved.
   P0 naturally stays flat until 15:30 and may skip a below-threshold day.
7. To view the dashboard, open PowerShell **in the unzipped project directory**:

   ```powershell
   .\ninjatrader\Watch-Dashboard.ps1 -RunId p0-playback-001
   ```

   It runs Python, reads the CSV logs every 5 seconds and opens `dashboard.html` in
   your browser. The page refreshes every 5 seconds. If `python` is not on PATH,
   pass `-PythonExe 'C:\path\to\python.exe'`. If your Documents folder is relocated
   (e.g. OneDrive), pass `-NinjaTraderHome 'your actual NinjaTrader 8 folder'`.
   If PowerShell blocks the helper, use the Python command directly instead of
   changing the machine's execution policy:

   ```powershell
   python main.py dashboard --nt-events 'C:\Users\you\Documents\NinjaTrader 8\MNQPaper\p0-playback-001\Sim101_MNQ_12_26_P0_events.csv' --out dashboard.html
   ```

   Use the actual log filename from your run directory; the exact name varies.
   Open `dashboard.html`. Rerun the command to refresh it, or use the watcher.
8. For each evaluation account's strategy instance set **Account stage=Evaluation**;
   after passing, set **Funded**. The classifier controls daily targets. This build
   restricts actual order routing to Sim101/Playback101, so changing the label does
   not trade a real prop-firm account or enable live capital. Keep loss limits in
   both stages. It does not model a firm's trailing-equity or consistency rule yet.

## Acceptance before judging performance

Replay the same tick sample twice in separate fresh run directories and compare
signal directions, fill paths and P&L; native order IDs may differ. Test a threshold
boundary, tick stop/time-exit collision, C1 stop/target collision, scheduled early
close, roll day, and disconnect/reconnect at 15:40. Confirm one position, one native
working stop, no new entries after a fault, and a reconciled flatten event. Sim101
can introduce simulated execution delay; playback can call executions synchronously.
Do not infer fills from chart markers or combine High Order Fill Resolution with
Tick Replay for this multi-series strategy. Keep limit-on-touch disabled.

The heartbeat detects stale data on Sim101; playback relies on the observed tick
timeline rather than the PC clock. Stops are working **NinjaTrader simulation
orders**, not stops held on an external broker server. Keep NinjaTrader running.
Gaps, disconnects, process termination and partial/order faults require inspection.
Keep each session's `.entry` lock: it prevents same-day re-entry across restarts and
strategy arms. Do not delete it to re-enter an active session. Start a fresh run ID
for another independent replay dataset; normal forward restarts still retain the
account/instrument/date lock. Reconcile/flatten your own paper position before
disabling or re-enabling. No existing logs are overwritten.

Native files under `Documents\NinjaTrader 8\MNQPaper\<run-id>\` include ticks,
events and 30-question loss reviews. Send those files and your friend's strategy/
trade export for analysis. Also supply actual fee totals, evaluation firm, account
size, trailing drawdown and consistency rules. Never send passwords or API keys in
chat. At least 30 clean eligible forward sessions and a passing untouched holdout
are needed before claiming the approach has an edge.
