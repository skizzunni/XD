# Repair the R2 calendar error and open the dashboard

The October 7 screenshots show two failures. NinjaTrader rejected a saved
daytime calendar because R2 needs the reviewed full-session columns. Throwing
that initialization error disables the strategy. The Windows launcher separately
stopped because `ninjatrader\MNQPlanPaper.cs` was absent from the extracted folder,
so the browser never started. The screenshot does not establish why that source
file was missing.

The repair package includes an identical text source backup and SHA256 manifest,
installs a clearly named full-session calendar and opens the read-only browser
even when strategy installation reports an error. It preserves old differing
files and existing trade/risk records.

1. Disable every old **MNQPlanPaper** instance. Confirm Sim101 has no open MNQ
   position or working MNQ order before changing source. Stop the old dashboard
   launcher with **Ctrl+C**.
2. Download the complete repair ZIP, right-click it and choose **Extract All**.
   Open the extracted project folder containing **START-SIM101.cmd**. Leave the
   packaged source files there; the launcher copies them into NinjaTrader.
3. Double-click **START-SIM101.cmd**. Enter **r2-repair-sim101-001** if unused,
   otherwise a different unused run ID. Keep the window open. It prints
   **R2 Frozen calendar CSV:** followed by the full path. It also opens a browser
   address such as `http://127.0.0.1:8765/`; use the address actually printed.
   Existing different files are preserved under
   `NinjaTrader 8\MNQPaper\install-backups\...`.
4. In NinjaTrader choose **New → NinjaScript Editor**, then press **F5**.
   If compilation fails, retain the error table and keep the strategy disabled.
   Actual NinjaTrader API compilation cannot be performed in the Linux cloud.
5. Configure a fresh **MNQPlanPaper** instance using these values:

   | Setting | Value |
   | --- | --- |
   | Instrument / data series | **MNQ DEC26**, **Minute**, **5** |
   | Trading hours | **CME US Index Futures ETH** |
   | Account / account stage | **Sim101 / Funded** |
   | Strategy arm / R2 exit profile | **R2 / TrendRunner** |
   | Research → Paper run ID | The exact run ID entered in the launcher |
   | Data → Frozen calendar CSV | The exact installed **MNQCalendar-R2.csv** path printed by the launcher |
   | R2 adaptive paper sizing | **True** |
   | Paper contracts / maximum paper contracts | **2 / 10** |
   | Platform display time zone ID | **Eastern Standard Time** |
   | Allow historical orders | **False** |

   Your screenshots indicate the calendar path will probably be:

   ```text
   C:\Users\skyma\OneDrive\Documents\NinjaTrader 8\MNQCalendar-R2.csv
   ```

   Copy the printed path if it differs. Keep the existing explicit loss budget
   and verify fees. Starting quantity is fitted to remaining risk; adaptive
   sizing never increases the loss allowance. Use at least 60 days of actual
   individual-contract history and verify 20 completed cash sessions/positive
   ATR before expecting entries. See [R2-START-HERE.md](R2-START-HERE.md) for history
   and market-data setup.
6. Connect your real-time CME market-data source and enable the strategy. The
   dashboard shows configuration and entry checks before any trade. Confirm
   **R2**, the matching run ID and fresh quote timestamps. `CALENDAR_SELECTED`
   records any verified replacement of a saved legacy path; `STARTUP_FAILED`
   records any remaining calendar error and both paths.

If you only want to view existing logs, double-click **START-DASHBOARD.cmd** and
enter that existing run ID. It needs Python 3.12+ and the browser runtime; it does
not need the strategy source, calendar or dependency installation. When setup
reports an error, the browser can still display saved logs, but that does not
mean the strategy is running. Close old local-file dashboard tabs and use the
new browser server address. Pages refresh every five seconds without replacing
HTML files in OneDrive.

The latest screenshot also reports **Instrument with delayed data has been
detected**. A connected price feed can still be delayed. Sim101 does not upgrade
that entitlement; real-time CME data must come from the connected provider.
Freshness guards remain active. CME maintenance is **17:00–18:00 ET** each day;
R2 needs six fresh closed bars after reopening, so the first eligible evening
scan is around **18:30 ET**, subject to history, data, news, liquidity and risk
checks. The reviewed calendar enables October 7–30, 2026.
