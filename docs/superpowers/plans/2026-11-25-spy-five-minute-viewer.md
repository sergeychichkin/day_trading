# SPY Five-Minute Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backfill completed SPY five-minute regular-session bars through TWS from January 2000 and chart session VWAP and the exact existing daily levels at `/spy/5m`.

**Architecture:** A separate SPY downloader owns the IBKR contract, NYSE schedule, request ledger, and incremental SQLite cache. The local server serves one intraday month at a time, calculates VWAP from cached trade WAP and volume, and joins the Yahoo daily levels by New York session date.

**Tech Stack:** Python 3.13, pinned `ib_async`, pandas-market-calendars, SQLite, bundled Lightweight Charts, pytest and Playwright.

**Spec:** [SPY five-minute viewer design](../specs/2026-11-25-spy-five-minute-viewer-design.md)

## Global Constraints

- Start January 1, 2000. Target the latest NYSE regular session that has closed when the run starts. Never shift the start date to hide an outage.
- Read-only, native five-minute TWS `TRADES`, regular hours, with broker WAP. Keep all persisted intraday timestamps in UTC.
- Use a separate `data/spy_5m.sqlite3` cache. Preserve QQQ interfaces, SPY daily behavior, and existing uncommitted SPY daily fixes.
- Do not download from an alternate provider, place orders, or commit generated caches.
- Daily overlays are the existing `upper` and `lower` values, not re-anchored. Never substitute typical price for VWAP.

## Review Focus

- Incomplete first-week probe versus no usable January sessions; do not launch thousands of requests after an empty probe (Task 2).
- An early close, spurious close-boundary row, or DST change must not alter the expected bar grid (Task 1).
- A retry may repair missing WAP without overwriting accepted OHLCV or valid WAP (Task 1).
- A missing early bar or unusable positive-volume WAP makes subsequent session VWAP unavailable (Task 3).
- A stale month response and a missing daily cache cannot overwrite valid candles or show the wrong levels (Task 3).

### Task 1: Model sessions and persist five-minute observations

**Files:** Create `qqq_viewer/spy_5m_store.py` and `tests/test_spy_5m_store.py`.

**Interfaces:** `START = date(2000, 1, 1)`; `DEFAULT_DB = Path("data/spy_5m.sqlite3")`; `sessions(start: date, end: date) -> list[Session]`; `completed(now: datetime) -> list[Session]`; `expected_times(session: Session) -> range`; `FiveMinuteBar(time, open, high, low, close, volume, wap)` with `wap: float | None`; `FiveMinuteStore(path: Path, *, read_only: bool = False)`. Store methods: `configure(con_id: int, volume_unit: str) -> None`, `set_target(through: date) -> None`, `record(session: Session, status: str, bars: list[FiveMinuteBar], diagnostic: str = "") -> None`, `pending(target: list[Session]) -> list[Session]` and `read_month(month: str) -> dict`.

`read_month` returns `{"target": ISO date or None, "sessions": [...]}`. Each session row has `date`, `open_utc`, `close_utc`, `verified` (completed response plus complete bar grid and usable positive-volume WAP), `conflict_times` (epoch seconds), and `bars` (ascending `FiveMinuteBar` dictionaries). Return expected sessions too when a target was set; omit future sessions. Expose `latest_date() -> date | None` and `coverage(target: list[Session]) -> dict` for the month index and backfill summary; coverage aggregates `first_bar`, `last_bar`, `expected`, `received`, `missing`, `unverified_sessions`, and `conflict_count` without loading historical OHLCV into memory.

- [ ] Add `test_expected_regular_and_early_close_bars`: assert January 3, 2000 has 78 bars; November 28, 2025 has 42; neither grid contains its closing timestamp. Add `test_completed_cutoff_exact_at_early_close`: 17:59:59 UTC excludes that session; 18:00 UTC includes it. Assert the 09:30 New York open retains its wall-clock time across the spring DST switch while UTC changes.
- [ ] Add `test_store_repairs_only_missing_wap`: record positive volume and no WAP, retry the same OHLCV with WAP, then retry a different valid WAP and assert the first valid WAP remains and timestamp is flagged. Add `test_store_ignores_outside_grid_and_resumes_incomplete_sessions`: close-boundary and off-grid rows do not count; a timeout or missing bar remains pending; a complete valid session does not. Assert symbol/schema and volume-unit checks prevent accidental writes to the QQQ file and incompatible caches.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/test_spy_5m_store.py -q`; expect failures for the missing module.
- [ ] Implement the interfaces above. Build the NYSE schedule separately from QQQ's cutoff. Use an identifying schema marker, transactional writes, indexes on date/time, and durable attempt statuses. Validate finite OHLCV and price bounds, and map unavailable or invalid WAP to `None`; only require usable positive WAP on positive-volume rows. A timeout cannot make a session verified. Keep unverified and conflicting bars distinguishable; never create flat filler candles.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/test_spy_5m_store.py -q`; expect all tests to pass.
- [ ] Commit only the new store and its tests.

### Task 2: Implement and run the read-only TWS backfill

**Files:** Create `qqq_viewer/spy_5m_backfill.py` and `tests/test_spy_5m_backfill.py`; consume Task 1 interfaces and the existing SPY daily JSON (`bars[].time/open/close`).

**Interfaces:** `SpyHistory(port: int, client_id: int, *, min_interval: float = 2.0)` with `qualify_spy() -> ContractIdentity` and `request(sessions: list[Session], duration: str) -> tuple[str, list[FiveMinuteBar], str]`; `run_backfill(store: FiveMinuteStore, client, now: datetime, *, volume_unit: str, daily_cache: Path, probe_only: bool = False) -> dict`. Summary includes `through`, `first_bar`, `last_bar`, `expected`, `received`, `missing`, `unverified_sessions`, `conflict_count`, and optional `diagnostic`. CLI: `python -m qqq_viewer.spy_5m_backfill --port 7497 --volume-unit shares [--probe]`; provide `--db`, `--daily-cache`, and a positive client ID distinct from QQQ's.

- [ ] Add `test_spy_history_requests_native_rth_trade_bars`: fake the TWS boundary and assert contract qualification rejects a wrong ticker/currency or ambiguous identity; historical options are `"5 mins"`, `"TRADES"`, `useRTH=True`, `formatDate=2`, `keepUpToDate=False`, with UTC end at the last requested close. Assert `BarData.average` survives as WAP.
- [ ] Add `test_empty_old_probe_stops_but_isolated_gaps_resume`: a January probe with no usable sessions prevents bulk requests; one complete January session with isolated gaps permits the continuation when the recent probe passes. Failed recent probe, entitlement, disconnect, or unresolved pacing stops with a diagnostic. A repeated run requests only work needed after verification; a weekly response overlapping already-complete sessions changes neither their OHLCV nor their verification.
- [ ] Add `test_systematic_price_basis_mismatch_blocks_backfill`: if corresponding TWS open/close are consistently twice the daily snapshot across the overlapping probe sessions, stop before bulk loading and report the mismatch; accept comparable values with minor source differences and report comparison unavailable if the daily file is absent.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/test_spy_5m_backfill.py -q`; expect failures for the missing module.
- [ ] Implement `SpyHistory` and `run_backfill`. Reuse the existing socket-only handshake; do not send account, execution, or order requests. Use the qualified contract's unique ID, preserve the WAP field, and classify TWS errors. Batch by bounded exchange-calendar weeks initially; use larger windows only if a real probe validates the duration, counts, and runtime. Group accepted rows by session, retry incomplete sessions with bounded pacing, and skip already verified sessions. Compare open/close on complete overlapping probe dates; use a documented conservative threshold for a common multiplicative discrepancy, not exact same-source price equality. Handle `--probe` without scheduling the full history.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/test_spy_5m_store.py tests/test_spy_5m_backfill.py -q`; expect all tests to pass.
- [ ] Run the live probe on port 7497. Verify early and recent timestamp ranges, regular-hours coverage, volume unit and WAP, and compare to overlapping Yahoo daily bars. On a passing probe start the bounded resumable backfill; record actual coverage, any blocker, and whether the run is still in progress. Do not claim coverage based on the request parameters.
- [ ] Commit only the new downloader and its tests.

### Task 3: Serve the month payload and render `/spy/5m`

**Files:** Create `qqq_viewer/spy_5m_view.py`, `qqq_viewer/static/spy-5m.html`, `qqq_viewer/static/spy-5m.js`, `tests/test_spy_5m_view.py`, `tests/browser/test_spy_5m_chart.py`; modify `qqq_viewer/server.py`, `README.md`, and the applicable `EGM.md` files.

**Interfaces:** In `spy_5m_view.py`, `month_index(store: FiveMinuteStore) -> dict` returns `{"months": ["YYYY-MM", ...], "default_month": str | None}`; `month_payload(store: FiveMinuteStore, daily_cache: Path, month: str) -> dict` returns `{"month", "sessions", "coverage", "daily_status"}`. Each session includes the store's fields plus `upper` and `lower` (number or null), and bars additionally contain `vwap` (number or null). `coverage` includes `expected`, `received`, `missing`, `unverified_sessions`, and `conflicting`; `daily_status` is `available`, `missing`, or `unreadable`. Add `spy_5m_db: Path = DEFAULT_DB` to `make_server` as a keyword-only argument and `--spy-5m-db` to the server CLI. New routes: `/spy/5m`, `/api/spy/5m/months`, and `/api/spy/5m/month/<YYYY-MM>`.

- [ ] Add `test_vwap_uses_wap_and_resets`: WAP `100` at volume `10` and WAP `106` at volume `20` produce `104`, regardless of OHLC; next-session WAP `120` starts at `120`. Add `test_vwap_stops_after_gaps_or_invalid_wap`: VWAP is null before any positive volume, unchanged on an aligned zero-volume bar after initialization, and stays null after the first missing bar or unusable positive-volume WAP until the next session. Test exact `upper` and `lower` joins by New York date with intraday open deliberately different; where the daily snapshot is absent, corrupted or does not contain that date, preserve candles and leave the levels null.
- [ ] Add HTTP and Playwright tests for the new routes, supported month bounds and empty/cache states. Verify an early-close and a regular session show their own horizontal levels and VWAP without overnight joins; an internal gap stays visibly blank. A delayed former month response must not override the later selection. Verify QQQ `/` and SPY daily `/spy` still load.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/test_spy_5m_view.py tests/browser/test_spy_5m_chart.py -q`; expect failures for the missing routes and view.
- [ ] Implement the interfaces. Compute month VWAP against the expected grid, not just returned rows. Read the daily snapshot once per payload and join exact numeric values by session date. Use exchange-session whitespace for missing five-minute bars and separate line series per session; terminate daily lines at the final five-minute interval ending at the close without adding a fake candle. Keep the QQQ-style native Month picker, first-cached-session zoom, New York crosshair legend, and within-month panning. Give cache and coverage status without false completeness.
- [ ] Document TWS settings, probe and resume commands, coverage semantics, and `/spy/5m`. Record the actual observed ranges from Task 2, not hypothetical results.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest -q` and `git diff --check`; expect the full suite and whitespace check to pass. If live bars are available, verify at least an old and a recent month against the combined caches in the browser, and confirm the QQQ DB was not touched.
- [ ] Commit only the SPY intraday changes and tests; separate any pre-existing daily fixes in shared files from this commit.
