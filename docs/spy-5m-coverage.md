# SPY five-minute coverage record

## Live TWS probes — November 25, 2026

Source: SPY USD stock/ETF contract `756733` (primary listing ARCA), routed
through SMART on TWS port `7497`. Requests used native five-minute `TRADES`,
regular hours only, UTC timestamps, and the New York exchange session grid.
The reported head timestamp, January 29, 1993, was not specific to five-minute
bars and was not used as a coverage bound.

| Requested window | Request size | Result |
| --- | --- | --- |
| January 3–7, 2000 | One week | 0 of 390 expected bars; TWS 162, no data |
| January 3, 2000 | One day | 0 of 78; TWS 162, no data |
| Each month in 2000 | One month ending at each month's last NYSE close | 12 TWS 162 responses; 0 of 19,584 expected bars |
| Each month in 2001 | Same | 12 TWS 162 responses; 0 of 19,236 expected bars |
| Each month in 2002 | Same | 12 TWS 162 responses; 0 of 19,518 expected bars |
| Each month in 2003 | Same | 12 TWS 162 responses; 0 of 19,512 expected bars |
| January 2004 | One month | 312 of 1,560 expected bars; complete 78-bar sessions on January 23 and 26–28 |
| January 21 and 22, 2004 | One day per session | 0 of 78 each; TWS 162, no data |
| January 23, 26, 27 and 28, 2004 | One day per session | 78 of 78 each; earliest observed full session January 23 |
| January 29 and 30, 2004 | One day per session | 0 of 78 each; TWS 162, no data |
| February 2, 2004 | One day | 78 of 78 |
| November 24, 2026 | One day | 78 of 78, all with positive volume and usable weighted prices |

The January 2004 monthly response also contained 12 off-grid rows. They were
excluded rather than treated as regular-session bars.

## Published uninterrupted range

Auditing backward from November 24, 2026 found the last unresolved session
on **December 23, 2013**. The monthly request plus a one-day retry provided
**71 of 78** expected bars. Missing New York bar starts: **09:30, 09:35,
09:40, 09:45, 09:50, 09:55, 10:00**.

The next NYSE session, **December 24, 2013**, starts the published continuous
range. The cache through **November 24, 2026** contains **3,249 sessions /
252,450 bars** with no missing, unverified, or conflicting bars. The browser
opened December 24 at 09:30 New York time with VWAP and both daily levels.

To reproduce the checks without TWS or a cache write:

```powershell
.\.venv\Scripts\python.exe -m qqq_viewer.spy_5m_contiguous --verify --through 2026-11-24
```

To rebuild from TWS with the same cutoff, use disposable output paths:

```powershell
.\.venv\Scripts\python.exe -m qqq_viewer.spy_5m_contiguous --through 2026-11-24 --floor 2004-01-23 --volume-unit shares --db data/spy_5m_rebuild.sqlite3 --staging-db data/spy_5m_rebuild_staging.sqlite3
```

A rebuild uses the provider's data as it exists at run time. It is not a
replay of the original broker responses, which may be revised. The legacy
January probe remains available as a diagnostic on a separate cache:

```powershell
.\.venv\Scripts\python.exe -m qqq_viewer.spy_5m_backfill --volume-unit shares --probe --db data/spy_5m_probe.sqlite3
```
