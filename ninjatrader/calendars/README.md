# Dated December 2026 calendars

| Folder | Enabled entry dates | Warmup dates |
| --- | --- | --- |
| `mnq-dec26-sim101-2026-10-07-09` | October 7–9, 2026, for Sim101/current market data | August 24–October 6; 31 sessions |
| `mnq-dec26-2026-10-05-06` | October 5–6, 2026, for the requested playback | August 24–October 2; 29 sessions |

Each folder contains a reviewed `calendar.json` and its reproducible
`MNQCalendar.csv`. The Windows launcher installs the **Sim101** preset. The old
one-row `calendar.example.json` remains a synthetic schema example.

These files define dates and scheduled events, not price history. Actual
individual **MNQ 12-26** ticks and full 09:30–16:00 ET five-minute history are
required. Keep December-contract prices throughout warmup, including dates
before December became the customary lead contract. Do not substitute September
prices or silently merge a back-adjusted continuous series. This fixed-contract
setup calendar is not a front-month research contract map.

`trade_enabled=false` retains complete daily ranges for ATR20 but blocks entries.
The updated native strategy accepts this eighth CSV column and also accepts the
original seven-column format, whose rows default to enabled. Update the strategy
source and compile before using the new CSV. R1 also requires the ninth
`news_windows` column, exported from each release's actual timestamp for a pause
five minutes before through ten minutes after. P0/C1 still accept the original
seven/eight-column formats; R1 rejects them because release times are missing.
A missing future date blocks entries;
the Sim101 preset must be updated before October 12, 2026.

## Sources checked

Retrieved October 7, 2026 UTC. Each JSON's `sources` records the exact URL,
retrieval time and SHA-256 of its HTML snapshot. Original HTML bytes are preserved
as gzip files in `source-snapshots`; the recorded hash applies to the decompressed
HTML. This keeps the snapshot auditable after websites change.

- [NYSE hours and holidays](https://www.nyse.com/markets/hours-calendars): core
  session 09:30–16:00 Eastern, Labor Day September 7 excluded; no scheduled early
  closes in these windows. This uses the cash-session definition in the strategy;
  it does not claim Globex is closed throughout a cash holiday.
- [CME customary equity-index roll dates](https://www.cmegroup.com/trading/equity-index/rolldates.html):
  September 14 roll, September 18 expiry; December 14 roll, December 18 expiry.
  September 14 is marked as a non-entry warmup day. No NinjaTrader-specific roll
  setting is inferred from CME's customary policy.
- BLS [August](https://www.bls.gov/schedule/2026/08_sched.htm),
  [September](https://www.bls.gov/schedule/2026/09_sched.htm) and
  [October](https://www.bls.gov/schedule/2026/10_sched.htm) schedules: include
  payrolls September 4 and October 2; CPI September 11; timed releases for other
  included dates are retained.
- [BEA full schedule](https://www.bea.gov/news/schedule/full): GDP August 26 and
  September 30 at 08:30 ET; international trade October 6 at 08:30 and the
  affiliate-services data release at 10:00. The upcoming-only page omits those
  completed releases, so the full schedule was used.
- [FOMC calendar](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)
  and Federal Reserve Board [August](https://www.federalreserve.gov/newsevents/2026-august.htm),
  [September](https://www.federalreserve.gov/newsevents/2026-september.htm), and
  [October](https://www.federalreserve.gov/newsevents/2026-october.htm) schedules:
  September 16 decision at 14:00 and press conference at 14:30; October 7 minutes
  at 14:00 and consumer credit at 15:00. Board events September 30 at 15:25 and
  October 1 at 15:30 are late-news blocks. Both dates remain warmup only.

Earlier releases remain flagged and do not block a day under the frozen
15:25–16:00 exclusion rule. Releases after 16:00 are retained in the JSON but
do not set the late-news flag. Two warmup-only G.20 releases have no exact time
in the source and are recorded in `untimed_events`, without invented timestamps.

Coverage includes timed BLS, BEA and Federal Reserve **Board** schedule entries.
It is not an exhaustive feed of all publishers, regional Fed speakers or
unscheduled news. Recheck official schedules before each enabled session. The
October 5–6 playback snapshot was retrieved after those dates; each release's
`revision=null` explicitly means no archived revision history was obtained.
These setup files do not establish point-in-time research provenance or profits.

## Reproduce the CSV

Choose a new output name; the exporter preserves existing frozen files:

```powershell
python main.py export-nt-calendar --calendar ninjatrader\calendars\mnq-dec26-sim101-2026-10-07-09\calendar.json --out MNQCalendar-new.csv
```

The CSV's first line records the JSON hash; native run logs also record the CSV
hash. Calendar verification does not prove that NinjaTrader has downloaded the
necessary history or that native compilation/order handling works.
