# Install the fixed bot and start R2 on Sim101

The uploaded diagnostics confirm a contract-name comparison defect, old **P0**
settings and delayed MNQ data. The update accepts `MNQ DEC26` and `MNQ 12-26` as
the same contract and adds the full-session **R2** paper experiment.

For the October 7 **R2 needs the reviewed full-session calendar** initialization
error, or a launcher that cannot find `MNQPlanPaper.cs`, follow
[FIX-R2-STARTUP.md](FIX-R2-STARTUP.md). The repair package includes a checked source
backup, installs a dedicated R2 calendar, and starts the browser independently of
strategy installation. NinjaTrader still needs F5 and the settings below.

1. **Disable the old bot.** In NinjaTrader, disable every old `MNQPlanPaper`
   instance. Confirm Sim101 has no open MNQ position or outstanding MNQ orders.
   Keep existing logs, risk history and setup claims.
2. **Install the complete update.** Download the fixed ZIP, right-click it →
   **Extract All**. Open the extracted project folder. Close the old dashboard
   launcher with **Ctrl+C** and double-click **START-SIM101.cmd** in the new folder.
   At its run-ID prompt, use **r2-sim101-001** if unused, otherwise another unused
   ID such as `r2-sim101-002`. This installs source and the October full-session
   calendar and opens the matching browser dashboard. `START-DASHBOARD.cmd`
   alone preserves installed strategy source and cannot install this update.
3. **Compile.** Choose **New → NinjaScript Editor**, then press **F5**. Keep the
   strategy disabled if compiler errors appear. The cloud cannot compile actual
   NinjaTrader API bindings; the compiled standalone rule checks are separate.
4. **Connect real-time CME data.** Your Log says “Instrument with delayed data
   has been detected: MNQ DEC26.” Check your provider/NinjaTrader account portal's
   market-data entitlement and connect a source with **real-time CME futures
   data**. Reconnect after any entitlement change. Sim101 provides simulated
   execution; it does not make a delayed data subscription real-time. Leave
   Playback disconnected. Check that quote timestamps follow the current clock.
5. **Create the correct chart.** Select **MNQ DEC26 / MNQ 12-26**,
   **Type=Minute, Value=5**, and **Trading hours=CME US Index Futures ETH** (your
   provider's MNQ equivalent must include the full overnight session). Use
   Eastern display time, **DoNotMerge** individual-contract history, and at least
   **60 days to load**. Download actual historical **Last ticks** for the
   December contract, August 23–October 6, 2026, if the provider has not supplied
   enough history. Closed five-minute bars still build the lagged cash ATR20;
   a missing interval can invalidate the warmup.
6. **Add a fresh strategy instance.** On that chart, right-click → **Strategies**
   → add **MNQPlanPaper**. Set the properties explicitly:

   | Property | Value |
   | --- | --- |
   | Account | **Sim101** |
   | Strategy arm | **R2** |
   | Account stage | **Funded** |
   | Research → Paper run ID | **r2-sim101-001**, or the unused ID entered in the launcher |
   | Frozen calendar CSV | The installed `MNQCalendar-R2.csv` in your actual NinjaTrader user-data folder; copy the exact path printed by the launcher |
   | Platform display time zone ID | **Eastern Standard Time** |
   | Allow historical orders | **False** |
   | Use break-even protection | **True** |
   | Maximum tick gap | **90 seconds** |
   | R2 exit profile | **Fixed** for the baseline, or **TrendRunner** for the new longer-trend paper test |
   | Paper contracts (R2 only) | **2** for the requested larger paper test; selectable **1–10** |
   | R2 adaptive paper sizing | **True** to adjust quantity from completed outcomes; **False** retains fixed quantity |
   | R2 maximum paper contracts | **10**, or your chosen maximum at least as large as the starting quantity |
   | Session loss limit and fees | Your chosen loss budget; default $100. The supplied statement shows **$1.50 per MNQ round trip**; verify your own account's costs |

   The existing saved P0/R1 instances retain their settings after a source update;
   selecting R2 explicitly is required. The orange “Primary series must be
   5-minute MNQ” messages are fixed by the chart/data-series settings above.
   R2 now resolves a missing or recognized legacy daytime calendar to the
   checksum-verified installed R2 calendar. An explicitly selected full-session
   calendar still undergoes normal validation; malformed files are not replaced
   silently. Initialization failures are recorded as `STARTUP_FAILED` for the
   browser, including the requested and installed paths.
7. **Enable and check the browser.** Look for **R2**, **REALTIME_STARTED**,
   **FEED_READY**, positive ATR20 and `completed_atr_sessions=20`. Your contract
   should map to `contract_key=MNQ 12-26`. The browser shows the exact run ID,
   futures risk day, profile, quote age, entries/orders, closed trades and loss
   reviews. Leave its launcher open and keep the PC awake. Pending startup/history
   checks are visible before any trade. It does not place orders itself.

R2 scans the open futures session, including overnight, subject to quality,
liquidity, news and risk controls. It needs six fresh closed bars, stops new
setups fifteen minutes before the reviewed close and flattens five minutes
before it. CME closes **17:00–18:00 ET daily** and over the weekend. The calendar
currently covers enabled dates **October 7–30**; update it before November.
Read [R2-FULL-SESSION.md](R2-FULL-SESSION.md) for all rules and validation limits.
For the adaptive runner test, use **r2-adaptive-sim101-001** (if unused) in both the
launcher and Research → Paper run ID, and set **R2 exit profile=TrendRunner**.
Read [R2-ADAPTIVE-SIZING.md](R2-ADAPTIVE-SIZING.md) for growth, reduction,
risk-fitting and restart rules. The original fixed runner remains available
with adaptive sizing disabled and a different unused run ID.
See [R2-EXIT-EXPERIMENT.md](R2-EXIT-EXPERIMENT.md) for the comparison procedure.
