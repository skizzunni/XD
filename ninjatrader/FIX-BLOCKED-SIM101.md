# Repair the October 7 blocked Sim101 run

The user subsequently requested full-session paper trading. Use
[R2-START-HERE.md](R2-START-HERE.md) for the current package and R2 settings.
The R1 recovery steps below remain a record of the cash-session fix.

Your uploaded report identifies an actual contract comparison defect: NinjaTrader
reports `MNQ DEC26`, while the frozen calendar uses `MNQ 12-26`. The old code
rejected that name in both entry checks and ATR history. This version recognizes
those names as the same December 2026 contract. Other expiries still fail the
calendar check. Raw logs, session claims and risk history are preserved.

The report also records **P0**, not R1, and a tick timestamp approximately **602
seconds** behind the report time. Your NinjaTrader Log explicitly says
**“Instrument with delayed data has been detected: MNQ DEC26.”** Current-market
Sim101 testing requires a real-time CME feed. The orange 06:38 errors say the
primary series was not five-minute MNQ; those are configuration errors rather
than compiler errors. The later 06:47 strategy start and report are a separate
stage of the same troubleshooting history.

1. Disable every existing `MNQPlanPaper` instance. Confirm Sim101 has no open MNQ
   position or outstanding MNQ orders before replacing source. Keep existing
   trade logs and risk files.
2. Download the fixed ZIP, choose **Extract All**, and open its extracted project
   folder. Close the old dashboard launcher with **Ctrl+C**. Double-click
   **START-SIM101.cmd** in the new folder. At the Paper run ID prompt, enter
   **r1-sim101-002**, provided that ID has not been used. This launcher installs
   strategy source and opens the matching dashboard; **START-DASHBOARD.cmd**
   alone does not install this strategy fix.
3. In NinjaTrader open **New → NinjaScript Editor** and press **F5**. Keep the
   strategy disabled if compilation reports errors. Cloud validation can compile
   the standalone contract helper, but cannot compile NinjaTrader API bindings.
4. Use a connection entitled to **real-time CME futures market data**. Review your
   provider/NinjaTrader account portal's market-data subscription if necessary,
   then reconnect. Sim101 supplies simulated execution, not a real-time data
   entitlement. Keep Playback disconnected for this test. Synchronize Windows
   date/time and use Eastern display time in NinjaTrader. Check that MNQ quote
   timestamps track the current clock rather than staying ten minutes behind.
5. Open an **MNQ DEC26 / MNQ 12-26** chart with **Type=Minute, Value=5** and at
   least **60 days to load**. Use **DoNotMerge** individual-contract history and
   a trading-hours template covering 09:30–16:00 ET. Retain the setup guide's
   historical Last tick download for August 24–October 6, 2026 if needed.
6. Remove the disabled old chart instance and add a fresh **MNQPlanPaper** instance
   on the five-minute chart. Set these properties explicitly:

   | Property | Value |
   | --- | --- |
   | Account | **Sim101** |
   | Strategy arm | **R1** |
   | Account stage | **Funded** |
   | Research → Paper run ID | **r1-sim101-002** (exactly matching the launcher) |
   | Allow historical orders | **False** |
   | Frozen calendar CSV | The `MNQCalendar.csv` installed in your actual NinjaTrader user-data folder |
   | Platform display time zone ID | **Eastern Standard Time** |
   | Maximum tick gap | **90 seconds** |
   | Session loss limit / fees | Your chosen paper loss budget and actual fees; defaults remain provisional |

7. Enable after compile, feed and history checks. The new run should log
   **REALTIME_STARTED**, identify **R1**, and map `contract=MNQ DEC26` to
   `contract_key=MNQ 12-26`. Look for positive `atr20` and
   `completed_atr_sessions=20` on the current enabled session. Persistent
   `ATR_WARMUP` means more valid completed history is still needed. New
   `DATA_GAP` records give the bar count, expected count and contract map;
   `DATA_QUALITY` records give price, volume, bid and ask. Resolve those actual
   records rather than repeatedly changing run IDs.

The browser updates every five seconds and displays tick age and delivery lag.
R1 scans fresh completed bars from **10:00–15:45 ET**; a healthy run may still wait
for a qualifying setup. The current frozen calendar permits October 7–9 only.
Recorded faults still require reconciling and restarting the native strategy;
restarting the browser does not clear them. Keep the launcher open.

See [SIM101-START-HERE.md](SIM101-START-HERE.md) for the full setup and dashboard
guide. This patch repairs a confirmed blocker; current-feed operation and native
order callbacks still require verification in NinjaTrader on your PC.
