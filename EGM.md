# Local QQQ and SPY viewer blueprint

## System Overview

The project stores Interactive Brokers QQQ one-minute historical data in a
SQLite cache, provides a local chart viewer, and runs opening-range backtests
that produce standalone HTML reports. The same server serves a SPY daily chart
from a separate Yahoo Finance JSON snapshot. See `README.md` for setup and CLI
usage.

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
- The SPY snapshot contains completed NYSE sessions and precomputed indicators.
  It does not share input or storage with QQQ.

## Global Side Effects

- Backfill communicates with local TWS and writes the configured SQLite cache.
- SPY refresh calls Yahoo Finance and publishes the JSON snapshot under a file
  lock; an older or less complete refresh cannot replace it.
- The viewer listens on localhost and reads the cache and static assets.
- The backtest reads configuration and cache data and writes an HTML report.

## System Verification

- Run `.\.venv\Scripts\python.exe -m pytest -q` from the repository root.
  Install test dependencies and a supported browser as described in `README.md`.

## SPY five-minute boundary

SPY five-minute acquisition has two paths:
1. Forward TWS build/refresh (`qqq_viewer.spy_5m_contiguous`) stages native regular-hours IBKR trade bars and publishes only an uninterrupted range to `data/spy_5m.sqlite3`, advancing forward from the installed endpoint without crossing a gap.
2. Backward Sierra + TWS extension (`qqq_viewer.spy_5m_extend` and `qqq_viewer.spy_5m_sierra`) reads consolidated US equities records from an external Sierra Chart `.scid` file, requires complete 1-minute RTH coverage and 5% price-basis parity against `data/spy_daily.json`, patches missing/incomplete pre-history sessions via TWS, copies the preserved TWS tail unchanged, and optionally backs up and atomically promotes the verified combined cache.

The local `/spy/5m` viewer reads that cache for price and cumulative session VWAP (computed from stored bar `wap` and volume) and the SPY daily snapshot for levels. Neither source is written by the chart server. Neither acquisition tool uses order endpoints. See [measured coverage](docs/spy-5m-coverage.md) for the January 2000 TWS probe, the TWS-only start, and the Sierra + TWS backward extension (`2008-05-05`–`2026-11-24`).

The SPY Entry 1 backtest consumes the verified intraday cache and the Yahoo daily snapshot offline. It writes new standalone HTML reports and does not refresh either source. See the [backtest commands and strategy rules](README.md#spy-five-minute-entry-1-backtest).
