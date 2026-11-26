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

## SPY five-minute boundary

SPY five-minute acquisition stages native regular-hours IBKR trade bars, then
publishes only an uninterrupted range to a separate SQLite cache. A refresh
extends the installed endpoint without crossing a gap. The local `/spy/5m`
viewer reads that cache for price and session VWAP and the SPY daily snapshot
for levels. Neither source is written by the chart server. The downloader uses
no order endpoints. See [measured coverage](docs/spy-5m-coverage.md) for the
distinction between the January 2000 probe and the published start.

The SPY Entry 1 backtest consumes the verified intraday cache and the Yahoo
daily snapshot offline. It writes new standalone HTML reports and does not
refresh either source. See the [dated reproduction steps](README.md#reproduce-the-november-24-2026-run).
