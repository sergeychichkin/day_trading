# QQQ Opening-Session Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Fetch and audit QQQ one-minute regular-session trade bars and browse them month by month, starting each month on its first available session's opening two hours.

**Architecture:** A Python CLI backfills a local SQLite cache through TWS. A separate loopback HTTP server reads the cache and serves the viewer plus one month of chart data per request.

**Tech Stack:** Python 3.13, SQLite, Interactive Brokers TWS Python API, pandas_market_calendars, tzdata, Lightweight Charts 4.1.1, and pytest.

**Spec:** [Approved viewer design](../specs/2026-11-22-qqq-opening-session-viewer-design.md)

## Global Constraints

- Work on the current branch, main. Never switch branches or create a worktree for this plan.
- Start on January 1, 2020 in New York time and end at the latest completed regular session as of the run.
- Use QQQ one-minute trade OHLCV, regular trading hours only. Use UTC in storage and America/New_York for dates and labels.
- Keep acquisition and browsing separate. The viewer runs without TWS. Historical requests are read-only.
- Preserve raw provider data, flag conflicts, and keep the first accepted value for each minute. Display missing coverage explicitly.
- Each selected month opens on the first available trading day's 09:30–11:30 window. Plot only that month's bars. Plot volume and its 20-bar SMA as specified.

## Review Focus

- A response contains some bars but times out before completion. Preserve the attempt, keep the session pending, and resume safely.
- A later response omits a minute filled by an earlier partial response. Determine coverage from reconciled bars.
- TWS volume units differ from those already in the cache. Stop ingestion before incompatible units enter the SMA.
- Fast A-to-B month selection yields replies out of order. Keep B on screen.
- Missing opening bars or an empty month must keep coverage visible and avoid fake prices.

## Files and responsibilities

- qqq_viewer/sessions.py supplies UTC session bounds and expected minute grids.
- qqq_viewer/store.py owns the SQLite schema, raw attempts, canonical bars, and coverage.
- qqq_viewer/tws.py wraps the official TWS API and exposes only contract qualification and historical requests.
- qqq_viewer/backfill.py provides the backfill and probe commands.
- qqq_viewer/server.py provides the read-only browser endpoints and serve command.
- qqq_viewer/static/ contains the HTML, CSS, chart JavaScript, and pinned chart library with license.
- tests/ contains calendar, cache, acquisition, server, and chart checks.
- pyproject.toml, .gitignore, and README.md capture setup, local artifacts, and Windows usage.

## Task 1 — Calendar and durable cache

**Files:** qqq_viewer/__init__.py, qqq_viewer/sessions.py, qqq_viewer/store.py, pyproject.toml, .gitignore, tests/test_sessions.py, tests/test_store.py.

**Interfaces:** sessions(start: date, end: date) -> list[Session] takes inclusive dates. Session carries date, open_utc, and close_utc, with UTC boundaries expressed as integer epoch seconds. completed_sessions(now: datetime) -> list[Session] starts at 2020-01-01 and includes only sessions with close_utc at or before now. Store(path: Path) exposes set_target(end_date: date) -> None and target_end_date() -> date | None for the full-backfill cutoff in ET. It also exposes record_attempt(session: Session, attempt: Attempt) -> None, missing_minutes(session: Session) -> list[int], and bars_between(start_utc: int, end_utc: int) -> list[Bar] for a half-open interval. Bar carries time, open, high, low, close, and volume. Attempt carries request metadata, raw rows, normalized bars, status, and diagnostic text.

- [ ] Add fixture-based tests for calendar boundaries, immutable canonical bars, and incomplete attempts. Pin expected assertions in the corresponding tests.

      assert first_session.date.isoformat() == "2020-01-02"
      assert thanksgiving_friday_minutes == 210
      assert (pre_dst_open_utc_hour, post_dst_open_utc_hour) == (14, 13)
      assert first_opening_bar == bars_after_conflicting_retry[0]
      assert conflicting_raw_values == [100, 101]
      assert session_remains_pending_after_timeout
      assert missing_after_complementary_responses == []

- [ ] Run python -m pytest tests/test_sessions.py tests/test_store.py -q and verify the absent modules fail.
- [ ] Implement the interfaces above. Use the NASDAQ calendar and [open, close) minute grid. Persist request records and raw timestamp/OHLCV values, accepted canonical bars, and conflict flags atomically under a single writer transaction. Index UTC bar timestamps and attempt/session lookups. Retain confirmed bars from partial responses as evidence and keep timeout-only coverage unverified until a completed response or explicit coverage reconciliation. Reject malformed or out-of-window bars from canonical results while retaining the raw rows.
- [ ] Run the same command and require all calendar and cache tests to pass. Commit the scoped implementation and tests on main.

## Task 2 — TWS probe and resumable acquisition

**Files:** qqq_viewer/tws.py, qqq_viewer/backfill.py, tests/test_backfill.py, README.md.

**Interfaces:** TwsHistory(port: int, client_id: int, timeout_seconds: float) connects to localhost, qualify_qqq() -> ContractIdentity resolves QQQ STK SMART USD with NASDAQ primary listing, and request_session(contract: ContractIdentity, session: Session) -> Attempt returns the audited result. run_backfill(store: Store, client: HistoryClient, now: datetime, probe_only: bool) -> BackfillSummary coordinates preflight, retries, and coverage. Use a duck-typed client in tests.

- [ ] Add tests for the exact request parameters and resume behavior using a fake client and fake clock.

      assert request_parameters == ("1 D", "1 min", "TRADES", True, 2, False)
      assert incomplete_sessions_retried == [missing_session.date]
      assert len(unique_times) == len(canonical_rows)
      assert requests_after_volume_unit_change == 0
      assert summary.failed_window == expected_session_date
      assert successful_requests_remain_available_after_disconnect

- [ ] Run python -m pytest tests/test_backfill.py -q and verify failure before implementing the client and CLI.
- [ ] Implement python -m qqq_viewer.backfill --probe and the same command without --probe for full acquisition. Expose --port, --client-id, and --db (default data/qqq.sqlite3). Record the target for a full run before requesting windows. Probe mode leaves the full-run target unchanged. Probe the first January 2020 session using a completed response, confirm OHLCV and resolve volume units from the applicable TWS API setting, retaining the raw values and recording the unit in the cache. Validate the same unit on refresh and fail before mixing incompatible units. Use each session close as the explicit UTC request endpoint and filter incoming bars against that session. Keep one request in flight, start with one regular session per request, maintain a monotonic delay between requests, and apply bounded backoff on retryable errors. Cancel timed-out requests, correlate callbacks by request ID, and distinguish broker errors from truly empty responses. Visit sessions oldest first. Query the cache for gaps before each window and skip previously verified sessions, including the initial probe date when it is already verified and the unit is unchanged. Print target range, received range, missing minutes, unresolved conflicts, and failures. Exit nonzero when acquisition remains incomplete.
- [ ] Run python -m pytest tests/test_backfill.py tests/test_store.py -q and require passing tests. Commit the scoped implementation and tests on main.

## Task 3 — Month endpoint and browser viewer

**Files:** qqq_viewer/server.py, qqq_viewer/static/index.html, qqq_viewer/static/chart.js, qqq_viewer/static/style.css, qqq_viewer/static/vendor/, tests/test_server.py, tests/browser/test_viewer.py, README.md.

**Interfaces:** GET /api/months returns month availability and default_month. GET /api/month/YYYY-MM returns an object with month, bars, sessions, and coverage. Each bar has the Bar fields plus volume_sma_20, which is null when the window is too short. Each session has date, open_utc, and close_utc. Coverage includes expected, received, missing, and conflicting minute counts plus status, using the recorded completed-session target. Bars and warmup come from one consistent cache snapshot. The HTML loads its assets locally.

- [ ] Add endpoint and browser fixture checks.

      assert next_month_payload["bars"][0]["volume_sma_20"] == 10.5
      assert all(server_bars_belong_to_selected_month)
      assert malformed_month_status == 400
      assert empty_month_payload["bars"] == []
      assert displayed_month_after_reversed_responses == "2020-02"
      assert displayed_hover_open == cached_open
      assert displayed_hover_volume == cached_volume

- [ ] Run python -m pytest tests/test_server.py tests/browser/test_viewer.py -q and check that the tests fail before the server and viewer exist.
- [ ] Implement python -m qqq_viewer.server --db data/qqq.sqlite3 --port 8765. Serve only on 127.0.0.1. Query at most 19 preceding bars for warmup and emit only selected-month bars. Generate month choices from the target and available coverage, default to the latest with data, and show clear status for empty months. Use local chart assets, a month picker, New York time formatting, vertical session-open lines, candles, and a volume histogram plus SMA. Preserve the session-open marker on the time scale when the first minute is missing. Use a latest-selection guard for asynchronous requests. Zoom to the 120 opening minutes starting at 09:30 and allow ordinary horizontal pan and zoom. Show OHLCV under the crosshair.
- [ ] Run python -m pytest tests/test_server.py tests/browser/test_viewer.py -q with the browser test dependency installed. Check initial 09:30–11:30 zoom, separator positions, navigation to the next session by horizontal drag, empty and incomplete status, and timestamp formatting across daylight saving. Commit the scoped implementation and tests on main.

## Task 4 — Live TWS backfill and end-to-end acceptance

**Files:** README.md. Runtime data and local logs go in ignored data/.

- [ ] Run python -m pytest -q and check that the full suite passes.
- [ ] Confirm the local TWS listener and settings. Run python -m qqq_viewer.backfill --port 7497 --probe, adjusting the port if the verified listener changed. Verify actual January 2020 timestamps, one-minute alignment, OHLC, volume units, and expected session coverage. If connectivity, permission, or early data fails, record the exact error and affected range and recover where authorized before starting the full run.
- [ ] Run python -m qqq_viewer.backfill --port 7497 and monitor progress through the cutoff. Retry recoverable failures and reconcile completed coverage. Run the command again and confirm verified sessions are skipped and canonical bar counts do not increase from duplicates.
- [ ] Run python -m qqq_viewer.server --db data/qqq.sqlite3 --port 8765. Verify the real viewer on two populated months. Cross-check hover OHLCV and SMA against cached data, including a session open and a month boundary, and verify early close and DST handling.
- [ ] Update README.md with tested PowerShell setup and commands, including the official TWS Python API source, the viewer URL, and how to read coverage diagnostics. Report actual first and last bar, target cutoff, acquired counts, gaps, and any conflicts. Commit documentation changes only. Leave the local viewer running.

## References

- [IBKR historical bar request](https://ibkrcampus.com/docs/tws-api/doc/market-data-historical/historical-bars/requesting-historical-bars)
- [IBKR historical request pacing and step sizes](https://interactivebrokers.github.io/tws-api/historical_limitations.html)
- [IBKR historical availability](https://ibkrcampus.com/docs/tws-api/doc/market-data-historical/unavailable-historical-data)
- [IBKR volume units setting](https://ibkrcampus.com/docs/tws-api/protobuf/api-settings-config)
- [Trading calendar implementation](https://github.com/rsheftel/pandas_market_calendars/blob/master/pandas_market_calendars/calendars/nyse.py)
