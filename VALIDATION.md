# Implementation review and validation

Validated in the cloud development machine. Native NinjaTrader assemblies,
Windows PowerShell execution, actual MNQ ticks/quotes and account fees are not
available here; native compilation/playback and profitability are unverified.

## Checks completed

- 144 automated tests: signal thresholds/freeze, ATR lag, Eastern/DST, early closes,
  rollover, news, bad/duplicate data, slippage caps, quote cost invariants,
  stop/time/target tick ordering, recovery reconstruction, unresolved-position
  preservation, account stages, net break-even/gap loss, research freeze/holdout
  lock and diagnostics/dashboard import.
- Adaptive sizing tests cover growth from 2 through 10, loss streaks, weak
  windows, faults, a single outlier winner, quantity-independent net R,
  strictly prior/profile-specific observations, exact cash boundaries and
  actual learned-quantity entries, fees, loss diagnostics and replay dashboards.
  Fixed modes retain their quantity; fixed comparison CLIs disable sizing.
- Two matching 48-session adaptive TrendRunner synthetic runs processed
  66,240 ticks and 1,260 positions each, actually using quantities 2–10.
  Every position reconciled to one sizing sample; no overlapping positions or
  reused entry IDs occurred, and each entry fit the remaining explicit budget.
  The stress configuration deliberately used a $1,000 session budget; it does
  not establish that ten contracts fit the default $100 or real market setups.
  Event SHA256:
  `a6860d8ae53e60891c014aad7f636d8f494f0703dbcf70f0a1536a4b50b59915`.
  Trade SHA256:
  `79c9e06069320dd54df1756e9a27e345f32915ca755aefaba4b864590b0cb30a`.
  Synthetic outcomes are behavior checks, not profitability evidence.
- 2,000 seeded random bid/ask, fee and slippage combinations checked P&L identities.
- A 400-session synthetic stress file contained 156,000 ticks. P0 closed 380 trades
  and produced 76 loss reviews with 30 questions each; no data faults. C1 had no
  qualifying setups in that broad fixture, so dedicated entry/stop/target tests
  exercise its execution paths. Synthetic P&L is not market evidence.
- Repeated replay produced identical event hashes and trades.
- R1's 400-session, 156,000-tick synthetic stress produced 4,560 closed trades,
  with twelve trades per eligible session and no overlapping positions or reused
  entry IDs. Two full runs had identical event hashes, trades and learning state.
- R1 tests cover repeated long/short entries, re-entry after stops, cumulative
  risk/targets, independent loss reviews, bounded quality tightening/recovery,
  exclusion of faulted samples and future results, news pauses/flattening and
  warmup-only dates. Prior poor outcomes change actual entry acceptance.
- R2's original fixed-exit implementation (commit `288f3ce`) passed two
  400-session full-session synthetic runs, each processing 552,000 ticks
  and closed 17,100 trades, with no overlapping positions or reused entry IDs.
  Both had identical event SHA256
  `7aaf2ab1f0bcae4918f0ccda3719f2801c6846fa12582fc74c20337efe485576`
  and trade SHA256
  `d7e7b49190e22f65a4f20fbbda0b6788f6de369d874c0cd9d6ecb2f7e2d29866`.
  Separate learning totals reconciled to 12,160 overnight and 4,940 daytime
  outcomes. Invented price paths cannot establish profitability.
- The current two-contract TrendRunner passed two identical 80-session runs
  with 110,400 ticks and 2,700 closed positions each, without overlap or reused
  entry IDs. Learning counts reconciled. Event SHA256:
  `80abb854ee8f61fe77a0e324edac8232540480781f0cdc4476d70ef397a20913`.
  Tests exercise longer holds, profile crossings, monotone trailing stops,
  actual gap losses and thirty diagnostics. Exit and 1/2/5/10 size comparison
  CLIs freeze filters/costs and retain the same loss budget.
- R2 tests cover Sunday reopening, midnight risk continuity, both DST weekends,
  maintenance/weekends, reviewed intraday breaks, early closes, quoted liquidity,
  cumulative losses, evening/08:30 news exits and later re-entry, lagged cash ATR,
  missing history/hours and unresolved exits. CLI replay exercises the SQLite
  tick index, frozen R2 configuration, saved 30-question losses and dashboard.
- The reviewed full-session calendar has 49 history/forward rows and 18 enabled
  October 7–30 dates. Tests verify official retained CME receipt hashes, all
  enabled open/close boundaries and reproducible thirteen-column native CSV
  export. Timed BLS/BEA/Fed news includes CPI, GDP, FOMC and late releases.
  One historical holiday multi-open segment is explicitly excluded.
- Python compilation and unused/unbound-name analysis passed.
- C# grammar parsing passed; this does not verify NinjaTrader API bindings.
- The actual C# contract helpers were extracted, compiled with a checksum-verified
  .NET 8 SDK and exercised against 8,226 cases, including all quarterly expiries,
  wrong years/products and seeded invalid identifiers, plus actual extracted
  session, learning and calendar-loader methods. Boundary assertions check
  futures dates, break/close deadlines, profile learning and connection-startup
  classification. Reproduce with
  `python scripts/check_native_contracts.py --dotnet <dotnet-executable>`.
  This compiles the helpers only; full NinjaTrader compilation remains unrun.
  Compiled assertions cover weighted partial-fill cash, pending executions and
  avoiding doubled risk from learning rows. A thousand seeded cases verify
  partial-fill P&L for sizes 1/2/5/10; another thousand verify trailing-stop parity.
  Three hundred random outcome streams check Python/C# adaptive policy parity,
  and 1,500 cash boundaries check fitted quantities. Actual extracted risk CSV
  loading reconstructs growth and cash without double counting normalized SIZE
  rows; missing SIZE/LEARNING pairs block restart. Legacy cash remains readable.
- Dashboard JavaScript syntax and functional checks passed for summary cards,
  380 trade rows, a 76-row loss filter, equity chart and 30-question review display.
- The current R1 dashboard also passed JavaScript syntax and DOM-harness checks
  for live open P&L, learning state, counts and win/loss filtering. Browser/Windows
  execution is still unrun. Native loss notifications no longer create fake exits.
- Current R2 dashboard DOM checks passed for separate daytime/overnight filter
  states and futures-day display. Python tests verify that native and replay
  learning snapshots retain both profiles and overnight trades keep their
  futures date across midnight. Existing diagnostic/all filters and delayed
  delivery displays passed again against the final generated JavaScript.
  Runner, open quantity, working stop and dashboard refresh timestamps also
  passed DOM checks. HTTP tests show a fresh snapshot while a quote remains
  ten minutes old. Partial fill rows produce one closed dashboard trade.
- Adaptive browser DOM and live HTTP snapshot checks verify learned size,
  automatic reductions, the last risk-fitted quantity, actual closed-position
  quantity and sizing in the diagnostic download. Sizing updates never create
  fake trades. Existing diagnostic filters and stale-feed displays passed again.
- The pinned, checksum-verified timezone package was tested with OS zone lookup
  disabled, matching Windows' need for packaged IANA time zones.

## Issues corrected during review

- Included completed early-close daily bars in ATR history while excluding them
  from entries; missing daily data resets warmup instead of silently bridging gaps.
- Allowed C1's first valid trade-through exactly at its formation boundary.
- Preserved failed replay logs and unresolved positions instead of losing evidence.
- Moved large-history deduplication from an unbounded RAM set to disk-backed SQLite.
- Used observed quoted costs in C1 risk and research economic floors without
  adding synthetic spread twice.
- Included no-trade calendar quarters in research concentration metrics.
- Counted each tick once in trade excursions and initialized marks with actual
  modeled entry/exit costs.
- Added capped native limit entries, expiry/cancellation, shared session locks,
  stale-feed detection, and explicit reconciliation of pre-existing positions.
- Buffered native tick exports and preserved prior files instead of duplicating
  history on a restart.
- Added warmup-only calendar dates without discarding their ATR history; verified
  that scheduled late news remains in CSV exports even when a roll/early-close
  reason takes precedence. Legacy native CSV headers remain supported in source.
- Added current Sim101 and requested playback calendars with official-source
  snapshots; checked reproducible CSV exports and synthetic scheduling for the
  requested playback dates. Real price data has not been downloaded here.
- Added a folder-independent Windows launcher. Python tests verify backup
  preservation, repeat installation, rejection of changed calendar bodies,
  failed-copy recovery and dashboard loading from a Sim101 ledger. Windows
  Known Folder calls, the CMD launcher and NinjaTrader remain unrun on Windows.
- Added verified Windows timezone support; escaped dashboard payloads and handled
  incomplete appended CSV rows.
- Replaced the native dashboard's repeated HTML writes with a read-only local
  browser server. Real HTTP tests cover waiting/startup diagnostics, live appended
  events, a simulated locked HTML file, malformed/nonfinite snapshots, read
  failures, occupied ports and refusing arbitrary file requests. The
  dashboard-only launcher preserves installed source and calendar. Package
  preflight catches missing strategy files before installation; run-ID selection
  uses the same explicit value for installation instructions and the dashboard.
- Added native startup path messages and premarket status telemetry without
  changing the entry window, order routing or risk limits. Native execution of
  these messages remains unverified until compiled on Windows.
- Added an entry-diagnostics summary and a downloadable report. Tests reproduce
  an early block hidden behind 1,500 tick-status rows, distinguish old-session
  checks and P0/R1 ledgers, preserve unknown causes, and avoid treating ordinary
  R1 setup rejections as session blocks. HTTP tests verify that the report removes
  routine status rows while preserving the earlier evidence without changing
  the source CSV or creating HTML files. Native trading code was unchanged.
- Repaired the confirmed `MNQ DEC26` / `MNQ 12-26` comparison defect from the
  October 7 diagnostic report. Native entry checks and completed-session ATR
  now recognize these names for the same expiry. Python replay also accepts the
  aliases, preserves raw contract labels and deduplicates ticks across aliases.
  Tests verify identical warmup/trades, repeated R1 entries, loss audits and
  learning when the same contract switches names. Other expiries stay separate.
- Added native receipt/quote-age telemetry and original price/volume/quote,
  session bar-count and connection-status evidence to fault records. HTTP and
  browser DOM checks verify the delivery-lag display and report. Old logs keep
  the distinction between an old timestamp and a proven delayed delivery.
  The user's NinjaTrader screenshot separately confirms delayed MNQ data;
  real-time CME entitlement is still a local prerequisite, and the freshness
  limit has not been relaxed.
- Initial connection/status notifications and missing initial paired quotes now
  wait without falsely latching a session failure. An actual interruption after
  the first healthy Sim101 quote, owned orders, later bad quotes and stale prices
  still preserve the fault/risk controls. Native execution remains unverified.
- Added a separate R2 full-session engine/native arm with frozen entry profiles,
  separate bounded learning, pre-close and break exits, reviewed Globex hours,
  full-session news and a futures-date loss budget. P0/C1/R1 rules remain intact.
  Updated launcher defaults and package preflight to install every R2 dependency
  and use the same explicit run ID for setup and dashboard instructions.
- Warmup-only dates require complete cash-session coverage for cash ATR20;
  thin older overnight ticks do not invalidate otherwise complete cash history.
  Enabled full-session dates still enforce their full-session coverage/freshness.

## Outstanding acceptance and research

R1/R2 are unvalidated adaptive paper experiments. Compile and exercise their native
owner lease, persisted risk/learning history, repeat setup claims, bracket cleanup,
news pauses and callback ordering on Windows. Cloud tests do not execute those
native methods. Compare adaptive and fixed-filter versions on untouched future
data; the P0/C1 research family intentionally rejects R1/R2. R2 also needs native
acceptance across the evening open, midnight, news pauses and maintenance.
Multi-contract native callbacks also require Windows acceptance for split fills,
entry remainders, late fills, stop quantity changes and restart after a partial
exit. The extracted accounting class was executed; NinjaTrader callback
sequencing was not.

Compile `MNQPlanPaper` in NinjaTrader 8; confirm native order and connection
callbacks with market playback, including stop rejection/cancellation, disconnect
at 15:40 and history-to-realtime transition. Confirm the native working simulation
stop in Control Center. Run the documented native/Python reconciliation rather
than assuming equivalence between the two fill models.

Supply real contract-specific tick/quote data, verified session/news/roll metadata,
actual fees and the firm's trailing-drawdown/consistency rules. Run real development,
validation and one holdout with at least 150 trades in each later segment. C1's
single-configuration PBO is not estimable and automatic promotion is blocked.
Threshold sensitivity, predeclared C1 ablations and at least 30 eligible clean
forward-sim sessions still need evidence. No strategy has passed those gates, and
the bot does not automatically change its active rules after a loss.
