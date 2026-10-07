# Install the fixed bot and start R2 on Sim101

The uploaded diagnostics confirm a contract-name comparison defect, old **P0**
settings and delayed MNQ data. The update accepts `MNQ DEC26` and `MNQ 12-26` as
the same contract and adds the full-session **R2** paper experiment.

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
   | Frozen calendar CSV | The installed `MNQCalendar.csv` in your actual NinjaTrader user-data folder |
   | Platform display time zone ID | **Eastern Standard Time** |
   | Allow historical orders | **False** |
   | Use break-even protection | **True** |
   | Maximum tick gap | **90 seconds** |
   | Session loss limit and fees | Your chosen paper loss budget and actual fees; $100/$1 defaults remain provisional |

   The existing saved P0/R1 instances retain their settings after a source update;
   selecting R2 explicitly is required. The orange “Primary series must be
   5-minute MNQ” messages are fixed by the chart/data-series settings above.
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
