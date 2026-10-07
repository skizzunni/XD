# Watch MNQ trade current market prices on Sim101

The errors in your screenshot came from running project commands at
`C:\Users\skyma`, where `requirements.txt` and `ninjatrader` do not exist.
The launcher below changes to its own extracted project folder automatically.
Your installed Python 3.14 satisfies the dashboard's Python 3.12+ requirement.
The pip upgrade notice does not require action.

## Install and open the dashboard

If the strategy is already running and the old watcher reports **WinError 5 /
Access is denied** while replacing `dashboard.html`, press **Ctrl+C** in that
watcher. From the fully extracted updated package, double-click
**START-DASHBOARD.cmd** and enter the exact run ID currently shown under
**Research → Paper run ID** in NinjaTrader. For the current test it is
`r1-sim101-001`. This starts the browser dashboard without installing strategy
source, changing the calendar or restarting NinjaTrader. Close the old local-file
dashboard tab and use the browser page opened by the new launcher.

The browser server reads the existing CSV logs and generates each page in memory.
It does not replace `dashboard.html` in OneDrive. Keep the launcher window open;
Ctrl+C stops the dashboard server and leaves the strategy running.

1. Download the updated GitHub ZIP. Right-click it → **Extract All**. Open the
   extracted `XD-...` folder containing `requirements.txt` and `START-SIM101.cmd`.
   Work from the extracted folder, not the ZIP preview.
2. In NinjaTrader, disable any existing `MNQPlanPaper` instance before updating.
   Stop your friend's MNQ strategy on Sim101 and reconcile any existing MNQ
   position before testing this strategy on that account.
3. Double-click **START-SIM101.cmd**. It installs the pinned dashboard dependency,
   copies the updated strategy and current calendar into your actual NinjaTrader
   Documents folder, and starts the browser dashboard. It asks for your exact
   **Research → Paper run ID**; enter the same value in NinjaTrader. Different existing files
   are backed up under `NinjaTrader 8\MNQPaper\install-backups\...`.
   Leave this window open. The browser opens even before logs exist, showing the
   selected run, log folder, other found runs and startup checks. Existing logs
   may belong to stopped runs. The dashboard does not place orders.
4. In NinjaTrader choose **New → NinjaScript Editor**, then press **F5**. If
   compilation fails, keep the strategy disabled and send the full error table.
   Native compilation has not been run in the cloud.

If the launcher cannot find NinjaTrader because you use a custom user-data
directory, open PowerShell **inside the extracted project folder** and run:

```powershell
python windows_setup.py --ninjatrader-home 'D:\your actual NinjaTrader 8 folder' install
python windows_setup.py --ninjatrader-home 'D:\your actual NinjaTrader 8 folder' dashboard --run-id r1-sim101-001
```

The Python launcher avoids requiring execution of a PowerShell script. It does
not change Windows execution policy, and it does not connect or enable NinjaTrader.

## Connect the current market and configure the strategy

1. Use NinjaTrader's **Connections** menu to connect your working market-data
   provider for current CME MNQ quotes and historical ticks. The **Simulated Data
   Feed** generates artificial prices; select your actual market-data connection
   for this test. A paid/data-enabled connection may be required by your provider.
   Leave Playback disconnected for this current-market test.
2. Set **Tools → Options → General → Time zone** to Eastern Time. The strategy
   property **Platform display time zone ID** must remain `Eastern Standard Time`.
3. Set **Tools → Options → Market data → Merge policy** to **DoNotMerge** for
   the individual December contract. Choose **New → Chart**, select
   **MNQ 12-26** (MNQ DEC26), and select
   **Type=Minute, Value=5**. In Data Series load at least **60 calendar days** of
   data and use a trading-hours template covering the entire 09:30–16:00 ET
   cash session. The added one-tick series needs actual historical ticks too.
4. If tick history is missing, use **Tools → Historical Data → Load → Download**:
   select **MNQ 12-26**, date range **August 24–October 6, 2026**, interval
   **Tick**, and **Last** data, then download through your connected provider.
   Reload the chart after the download. Provider retention and entitlement vary;
   five-minute bars do not replace the required tick history. Check the
   [official download guide](https://ninjatrader.com/support/helpguides/nt8/download.htm).
5. Right-click the chart → **Strategies** → add **MNQPlanPaper**:

   | Property | Value |
   | --- | --- |
   | Account | **Sim101** |
   | Strategy arm | **R1** |
   | Account stage | **Funded** |
   | Paper run ID | **r1-sim101-001** |
   | Frozen calendar CSV | Your actual `Documents\NinjaTrader 8\MNQCalendar.csv` (installed by the launcher) |
   | Allow historical orders | **False** |
   | Use break-even protection | **True** |
   | Session loss limit | **100** provisional; set your chosen simulation risk |
   | Round-trip fees | Replace provisional **$1.00** with your actual per-contract fee |
   | Enabled | Set **True** after compile and history checks |

   Keep the other initial thresholds. `Funded` removes the daily profit target;
   risk limits still apply. This setting describes the simulated profile.
6. Open **New → NinjaScript Output** and check Control Center **Log**. The current
   calendar enables **October 7–9, 2026 only**. Earlier dates build ATR and cannot
   open trades. `ATR_WARMUP` means insufficient valid completed history;
   `DATA_GAP` means the history/feed failed validation. Resolve those before
   expecting entries. Dates after October 9 require an updated reviewed calendar.

R1 checks each completed five-minute bar for a fresh trend, countertrend pullback
and breakout. It accepts qualifying setups from **10:00–15:45 ET** and has **no
daily trade-count cap**. It keeps one open contract, a stop and a 1.5 R target;
positions also exit after 30 minutes or by 15:55 ET. The cumulative session loss
budget still applies. A normal loss does not automatically end the session.
Scheduled releases pause entries from five minutes before until ten minutes
after the release; an open R1 position is flattened when that pause begins.
Keep NinjaTrader running and the connection healthy for its simulation orders.
See [R1-SCANNER.md](R1-SCANNER.md) for exact filters, learning and restart rules.

## Read the dashboard and assess results

The launcher opens a browser page served on your own PC, normally at
`http://127.0.0.1:8765/`. If that port is busy it chooses a free port and prints
the actual address. The page refreshes every five seconds; each request reads
the current log snapshot. No HTML replacement is needed. A
page without completed trades can still show events explaining why the strategy
is waiting or blocked. Keep **Paper run ID** identical in the strategy and
dashboard. Opening or restarting the dashboard can keep the same run ID.
Restarting the NinjaTrader strategy when existing tick logs are nonempty requires
a fresh run ID, e.g.
`r1-sim101-002`; run `python windows_setup.py dashboard --run-id r1-sim101-002`
to watch it. Session entry locks remain in place across run IDs. The dashboard
shows startup diagnostics before any trade. With the updated native source,
premarket ticks show `OUTSIDE_RTH`; R1 begins forming entries at 10:00 ET.

If there are no logs despite a matching run ID, open **New → NinjaScript Output**
and Control Center **Log**. The updated strategy prints the actual log folder at
startup. Initialization or historical-data loading may not have finished. Copy
any error text from these windows; “Waiting for strategy logs” alone does not
establish a compile error or a disconnected feed.

If entries are blocked and the ledger appears to contain only `LIVE_STATUS`,
use the new **Entry diagnostics** section above it. It groups recorded checks
for the latest logged session and shows their original evidence plus a suggested
check. These records can include earlier resolved checks; the page labels them
as recorded evidence rather than claiming a single current cause. A blocked flag
with no recorded explanation stays unknown.

The ledger defaults to **Diagnostic events**, filtering out `LIVE_STATUS` before
applying its display limit. You can choose **All events** or **Live tick updates**.
**Download diagnostic report** exports the recorded checks and all non-status
events from the selected run, including events too old for the visible table.
Send the relevant reason/evidence or that report when diagnosing a block.
Restarting the dashboard does not clear a strategy block. Confirm the actual
enabled strategy arm and feed state in NinjaTrader; a stale P0 row may belong to
an older or stopped instance even if you intend to run R1.

Orders/fills are visible immediately in Control Center **Orders/Executions**.
Completed trades appear in the dashboard with estimated fee-adjusted P&L,
drawdown, account stage and loss diagnostics. Reconcile the ledger with
NinjaTrader **Trade Performance**, including its actual commission template.
The dashboard is the bot's ledger; it does not independently query the account.

Use at least 30 clean eligible forward simulation sessions, calibrated costs,
drawdown and repeated losing periods to assess the bot. A short winning run does
not establish readiness for a funded account. Firm-specific trailing drawdown
and consistency rules still need to be supplied and modeled before that decision.

Source-calendar provenance and limitations are in [calendars/README.md](calendars/README.md).
