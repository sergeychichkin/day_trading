# QQQ opening-session viewer blueprint

## System Overview

The project stores Interactive Brokers QQQ one-minute historical data in a
SQLite cache, provides a local chart viewer, and runs opening-range backtests
that produce standalone HTML reports. See `README.md` for setup and CLI usage.

## Subdirectories & System Boundaries

- [qqq_viewer](./qqq_viewer/EGM.md): acquisition, cache, local HTTP server,
  strategy replay and report generation.
- `config/QQQ.toml`: example backtest ticker, cache and strategy settings.
- `tests/`: unit and browser tests; browser setup is described in `README.md`.

## Global Invariants

- `qqq_viewer.store.Store.record_attempt` preserves the first valid canonical
  bar and records later conflicting observations.
- `qqq_viewer.backtest_opening_range._verified_bars` and `run` reject
  incomplete, conflicting or mismatched backtest input.
- `qqq_viewer.sessions.Session.minutes` defines regular-session minutes with
  an inclusive open and exclusive close.

## Global Side Effects

- Backfill communicates with local TWS and writes the configured SQLite cache.
- The viewer listens on localhost and reads the cache and static assets.
- The backtest reads configuration and cache data and writes an HTML report.

## System Verification

- Run `.\.venv\Scripts\python.exe -m pytest -q` from the repository root.
  Install test dependencies and a supported browser as described in `README.md`.
