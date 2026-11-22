# QQQ viewer and opening-range backtest

## Overview

This Python package acquires Interactive Brokers historical bars into an
auditable SQLite cache, serves a local viewer, and generates opening-range
backtest reports.

## Interface

- `sessions.py`: `Session`, `sessions` and `completed_sessions` define
  exchange-calendar session windows.
- `store.py`: `Bar`, `Attempt` and `Store` normalize, persist and read bars,
  request attempts and coverage.
- `tws.py`: `TwsHistory` qualifies the QQQ contract and requests historical
  data; `backfill.py` provides `run_backfill` and its CLI.
- `server.py`: `make_server` serves the viewer and the `/api/months` and
  `/api/month/<YYYY-MM>` endpoints on localhost.
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

## Side Effects

- Writable `Store` operations create or modify a SQLite database and use WAL;
  the reader opens the database read-only.
- `TwsHistory` connects to local TWS and requests market history; backfill
  writes attempts and progress to the cache and terminal.
- `make_server` binds a threaded local HTTP server and reads assets and cache.
  `write_report` reads assets and writes generated HTML.

## Verification

- From the repository root: `.\.venv\Scripts\python.exe -m pytest -q`.
