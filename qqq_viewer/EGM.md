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
- `spy_5m_contiguous.py` is the supported forward build, refresh, and read-only
  verification command (`verify_published`, `validate_window`, `copy_window`,
  `identity`, `require_distinct`). It revalidates the attached staging snapshot
  within the publication transaction and never advances the published endpoint
  across incomplete sessions.
- `spy_5m_sierra.py` validates Sierra Chart `.scid` binary layouts (`SC_EPOCH`,
  `scid_identity`, `scid_first_date`), binary-searches contiguous session spans,
  and aggregates RTH records in O(1) bucket memory onto the 5-minute grid
  (`read_scid_five_minute_details`, `read_scid_five_minute_bars`). 1-minute OHLC
  bars (`open > 0`) round to 2 decimals with `wap` computed from volume-weighted
  1-minute typical prices `(H + L + C) / 3` (`None` when zero volume); `trades=1`
  ticks round to 4 decimals; `read_scid_five_minute_details` flags whether all
  1-minute slots in a bar-mode session are present.
- `spy_5m_extend.py` (`extend_spy_5m`, `main`) extends a published share-volume
  TWS cache backward using a `.scid` file (`--scid`, `--db`, `--out-db`,
  `--staging-db`, `--daily-cache`, `--floor`, `--backup`, `--promote`,
  `--reset-staging`). It stages complete-minute Sierra sessions passing 5% daily
  price-basis checks (`sierra-scid-1m`), batches/retries TWS requests for
  remaining pre-history gaps (`tws-1d-patch`), validates staging bars in-transaction,
  preserves the original TWS tail and `max(requested_through, source_end)`, and
  only promotes when unblocked and backed up (or `--allow-no-backup`).
- `spy_5m_snapshot.py` provides `read_snapshot` (safely copying DB + WAL and
  checkpointing private non-empty WALs before `?mode=ro`) plus `sqlite_paths`,
  `unlink_sqlite`, and `close_wal`.
- `spy_5m_store.py` owns the independent SQLite schema, `by_session`, and NYSE
  300-second grid. Verification requires a full successful response and positive
  total volume. Stored per-bar `wap` retains broker WAP for TWS sessions and
  1-minute typical-price weighted averages for Sierra sessions; duplicates may
  fill missing `wap` but cannot replace accepted values. Conflicting revisions
  are flagged. `attempts.diagnostic` records session provenance (`sierra-scid-1m`,
  `tws-1d-patch`, or TWS request diagnostics).
- `spy_5m_view.py` builds month payloads for `/api/spy/5m/months` and
  `/api/spy/5m/month/<YYYY-MM>`. Cumulative session VWAP resets at the open and
  is unavailable after a gap or unusable positive-volume `wap`. `upper` and
  `lower` come from the SPY daily JSON, joined on exchange-local date without
  rescaling. The API reports published bounds and requested cutoff separately,
  and a level status per session.
- `backtest_spy_5m.py` runs Entry 1 offline against the verified SQLite range
  (`data/spy_5m.sqlite3`) and the Yahoo/SPY daily snapshot. It checks cache identity
  and published NYSE endpoints, copies SQLite inputs to a private snapshot,
  validates/fingerprints/charts `wap`, and records both input fingerprints and the
  roles of any verified retained sessions before publication. Entry 1 checks session VWAP against daily Upper
  and can carry a buy stop to 09:35 ET the next NYSE session. Exit 3 can raise
  the stop once per session from the 09:45 ET candle on holding days 3–9 by
  default. It checks the two preceding Daily Lowers against verified opens.
  Exits 4 and 5 use completed daily bars for the next open. `spy_5m_replay.py`
  combines these rules on one stop, records the active rule, and keeps realized P&L and sizing cash in
  decimal. `spy_5m_report.py` writes standalone HTML, replacing the default
  report after the new report is ready. An explicit output path requires a
  new file.
