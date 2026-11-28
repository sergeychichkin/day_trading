# Local chart viewers and QQQ opening-range backtest

## Overview

This Python package acquires Interactive Brokers historical bars into an
auditable SQLite cache, serves a local viewer, and generates opening-range
backtest reports. It separately acquires Yahoo Finance SPY daily bars and
publishes a JSON snapshot for the SPY chart.

## Interface

- `sessions.py`: `Session`, `sessions` and `completed_sessions` define
  exchange-calendar session windows.
- `store.py`: `Bar`, `Attempt` and `Store` normalize, persist and read bars,
  request attempts and coverage.
- `tws.py`: `TwsHistory` qualifies the QQQ contract and requests historical
  data; `backfill.py` provides `run_backfill` and its CLI.
- `server.py`: `make_server` serves the viewer and the `/api/months` and
  `/api/month/<YYYY-MM>` endpoints on localhost. `/spy` is the SPY page;
  `/api/spy/daily` serves the configured SPY snapshot or an empty payload if
  its file is absent.
- `spy_daily.py`: `refresh` downloads SPY OHLCV and calculates both 10-session
  levels, the close EMA(20), and the volume SMA(20) before publishing.
- `opening_range.py`: `replay_session` models a session and returns a
  `SessionReplay`. `backtest_opening_range.py` loads settings, validates input,
  runs scenarios and provides the backtest CLI.
- `opening_range_report.py`: `write_report` generates the report HTML.
- [Browser interfaces](./static/EGM.md) provide viewer and report assets.

## Invariants

- `Session.minutes` uses open-inclusive, close-exclusive minute windows;
  `completed_sessions` requires a timezone-aware cutoff.
- `Bar.from_raw` validates minute alignment, finite OHLCV values, price bounds
  and nonnegative volume. `Store.record_attempt` retains attempts, keeps the
  first valid canonical bar and records differing later bars as conflicts.
- `Store.require_volume_unit` enforces consistent cache units. `TwsHistory`
  qualifies a USD QQQ stock contract and requests one-minute regular-hours
  trades.
- `backtest_opening_range._verified_bars` and `run` require verified,
  complete, conflict-free input with matching source and symbol metadata.
- `backtest_opening_range.output_path` protects the cache, SQLite sidecars and
  designated prior reports from being selected as the output.
- In SPY JSON, `through` is the last completed NYSE session checked, not
  necessarily the last cached bar. Missing scheduled sessions reset indicator
  windows. The server does not recalculate indicators or call Yahoo.

## Side Effects

- Writable `Store` operations create or modify a SQLite database and use WAL;
  the reader opens the database read-only.
- `TwsHistory` connects to local TWS and requests market history; backfill
  writes attempts and progress to the cache and terminal.
- `make_server` binds a threaded local HTTP server and reads assets and cache.
  `write_report` reads assets and writes generated HTML.
- `spy_daily.refresh` calls Yahoo and atomically replaces its JSON snapshot.
  A sibling lock serializes publication; an older cutoff or loss of existing
  dates rejects the replacement.

## Verification

- From the repository root: `.\.venv\Scripts\python.exe -m pytest -q`.

## SPY five-minute interfaces

- `spy_5m_backfill.py` owns SPY contract qualification and bounded TWS
  history requests. Its original January 2000 probe is a separate diagnostic.
- `spy_5m_contiguous.py` is the supported build, refresh, and read-only
  verification command. It revalidates the attached staging snapshot within
  the publication transaction and never advances the published endpoint
  across incomplete sessions.
- `spy_5m_store.py` owns the independent SQLite schema and NYSE 300-second
  grid. Verification requires a full successful response and positive total
  volume. UTC bars retain broker WAP; a duplicate may fill missing WAP but
  cannot replace accepted values. Conflicting revisions are flagged.
- `spy_5m_view.py` builds month payloads for `/api/spy/5m/months` and
  `/api/spy/5m/month/<YYYY-MM>`. VWAP resets at the open and is unavailable
  after a gap or unusable positive-volume WAP. `upper` and `lower` come from
  the SPY daily JSON, joined on exchange-local date without rescaling. The
  API reports published bounds and requested cutoff separately, and a level
  status per session.
- `backtest_spy_5m.py` runs Entry 1 offline against the publisher's verified
  SQLite range and the Yahoo/SPY daily snapshot. It checks cache identity and
  published NYSE endpoints, copies SQLite inputs to a private snapshot, and
  records both input fingerprints and the roles of any verified retained
  sessions before publication. Entry 1 checks session VWAP against daily Upper
  and can carry a buy stop to 09:35 ET the next NYSE session. Exit 3 keeps the
  initial stop through the first session after the fill and uses either of the
  two preceding session lowers on later sessions. It checks the same-date open when verified
  intraday data is available. The separate viewer checks the close as well.
  `spy_5m_replay.py` carries one Exit 3 position across sessions, activates a
  higher stop on the next bar, and keeps realized P&L and sizing cash in
  decimal. `spy_5m_report.py` writes standalone HTML, replacing the default
  report after the new report is ready. An explicit output path requires a
  new file.
