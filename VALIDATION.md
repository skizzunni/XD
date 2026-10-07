# Implementation review and validation

Validated in the cloud development machine. Native NinjaTrader assemblies,
Windows PowerShell execution, actual MNQ ticks/quotes and account fees are not
available here; native compilation/playback and profitability are unverified.

## Checks completed

- 54 automated tests: signal thresholds/freeze, ATR lag, Eastern/DST, early closes,
  rollover, news, bad/duplicate data, slippage caps, quote cost invariants,
  stop/time/target tick ordering, recovery reconstruction, unresolved-position
  preservation, account stages, net break-even/gap loss, research freeze/holdout
  lock and diagnostics/dashboard import.
- 2,000 seeded random bid/ask, fee and slippage combinations checked P&L identities.
- A 400-session synthetic stress file contained 156,000 ticks. P0 closed 380 trades
  and produced 76 loss reviews with 30 questions each; no data faults. C1 had no
  qualifying setups in that broad fixture, so dedicated entry/stop/target tests
  exercise its execution paths. Synthetic P&L is not market evidence.
- Repeated replay produced identical event hashes and trades.
- Python compilation and unused/unbound-name analysis passed.
- C# grammar parsing passed; this does not verify NinjaTrader API bindings.
- Dashboard JavaScript syntax and functional checks passed for summary cards,
  380 trade rows, a 76-row loss filter, equity chart and 30-question review display.
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

## Outstanding acceptance and research

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
