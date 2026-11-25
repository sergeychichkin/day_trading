# SPY Daily Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Download daily SPY bars and browse them with the QQQ-style Month picker and the approved indicators.

**Architecture:** A separate Python module downloads, validates, and computes the SPY series into an atomic local JSON snapshot. The existing local server exposes that snapshot and a dedicated chart page that loads it once and jumps to months without recomputing indicators.

**Tech Stack:** Python 3.13, `yfinance`, pandas-market-calendars, pytest, Playwright, bundled Lightweight Charts.

**Spec:** [SPY daily viewer design](../specs/2026-11-25-spy-daily-viewer-design.md)

## Global Constraints

- Start January 1, 2000; only include completed US regular sessions. Use Yahoo OHLC without dividend adjustment.
- Keep SPY storage separate and leave QQQ behavior intact.
- SMA uses 20 sessions including today; EMA uses 20-period close with an SMA seed. Distance lines use only the 10 sessions preceding today's open.
- Use the same native Month picker as QQQ, with January 2000 as the minimum and the latest cached month as the default.

## Review Focus

- Yahoo includes today's in-progress daily bar: exclude it (Task 1).
- Yahoo returns no usable data during a refresh: retain the previous cache (Task 1).
- A missing scheduled session must not silently turn “previous 10 sessions” into “previous 10 returned bars” (Task 1).
- A first-time viewer with no SPY cache should get a clear instruction, not a broken chart (Task 2).
- The picker can be cleared or point to a month with no bars: show a clear empty state rather than silently jumping elsewhere (Task 2).

### Task 1: Download, compute, and cache SPY daily data

**Files:** Create `qqq_viewer/spy_daily.py` and `tests/test_spy_daily.py`; modify `pyproject.toml`.

**Interfaces:** `refresh(path: Path, now: datetime, *, download: Callable | None = None) -> dict`; `python -m qqq_viewer.spy_daily --cache data/spy_daily.json`. The result and snapshot use `{"bars": [...], "missing_sessions": [...], "through": "YYYY-MM-DD"}`. Bars use `time` (ISO session date), `open`, `high`, `low`, `close`, `volume`, `volume_sma_20`, `ema_20`, `ema_direction` (`up`, `down`, `neutral`, or `null`), `upper`, and `lower`; unavailable indicators are `null`.

- [ ] Add fake-download tests. With prior opens `100`, highs `104`, and lows `98`, assert a current open of `200` yields `upper == approx(208)` and `lower == approx(196)` even if today's high and low change. Verify the first levels are on row 11 and remain available across a month boundary; verify the lookback rolls off its oldest observation. For closes and volumes `1..21`, assert row 20 seeds EMA and volume SMA at `10.5`, row 21 moves both to `11.5`, and use subsequent synthetic closes to verify green/red and flat persistence. Include a missing scheduled session and assert it is listed and levels are unavailable until 10 consecutive prior sessions exist again.
- [ ] Add download/cache tests: verify SPY, `start="2000-01-01"`, `interval="1d"`, `auto_adjust=False`, and an exclusive `end` after the last completed NYSE session. For a clock before market close, fake a row for today's date and assert it is absent. Test both flat OHLCV and single-ticker MultiIndex frames. Assert an empty or invalid download raises `ValueError` and leaves existing snapshot bytes unchanged.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/test_spy_daily.py -q`; verify failures refer to missing SPY behavior.
- [ ] Implement `refresh` and its CLI. Use the NYSE calendar with timezone-aware cutoff, sort by session date, validate finite OHLCV and OHLC bounds, and keep the most recent complete snapshot by writing a temporary sibling then replacing the destination. Report gaps, restart rolling inputs and EMA after missing scheduled sessions, and do not fabricate bars. Add `yfinance` to project dependencies and set its download flags explicitly.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/test_spy_daily.py -q`; expect all SPY data tests to pass.
- [ ] Commit the acquisition module, its tests, and dependency metadata.

### Task 2: Serve and display the SPY page

**Files:** Modify `qqq_viewer/server.py` and `README.md`; create `qqq_viewer/static/spy.html`, `qqq_viewer/static/spy.js`, `tests/test_spy_server.py`, and `tests/browser/test_spy_daily_chart.py`.

**Interfaces:** Extend `make_server(db_path: Path, port: int, *, spy_cache: Path = Path("data/spy_daily.json"))`; allow `--spy-cache` on the server CLI. `/spy` serves the page; `/api/spy/daily` returns the Task 1 snapshot, or an empty snapshot with `through: null` when the file does not exist.

- [ ] Add HTTP tests asserting `/spy` returns the SPY HTML; `/api/spy/daily` returns the fixture unchanged; a missing SPY cache returns `{"bars": [], "missing_sessions": [], "through": None}`; and `/` still serves the QQQ page.
- [ ] Add Playwright tests for a fixture spanning January and February 2000 with an empty intermediate month case. Assert `#month` has `type="month"` and `min="2000-01"`, defaults to the latest month, and changing it shows that month's first session in `#legend-time`. Capture chart series calls or inspect series data: volume and SMA have the same scale ID, distinct from price; EMA points include green `#10b981` and red `#ef4444`; both distance series contain the expected values. Check the empty states for a cleared picker, a month without bars, and a missing cache.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/test_spy_server.py tests/browser/test_spy_daily_chart.py -q`; verify failures identify absent SPY routes or chart behavior.
- [ ] Implement the routes and chart. Use the QQQ layout and styles, ISO dates to avoid timezone shifts, a shared lower volume-scale band, and point colors for EMA. Load all history once, put the selected month's first-to-last bar in view, allow panning across months, and report session coverage and cache status. Document install, download/refresh, server command, and `/spy` URL in `README.md`.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest -q` with browser support enabled per `README.md`; expect the suite, including the existing QQQ tests, to pass.
- [ ] Run `.\.venv\Scripts\python.exe -m qqq_viewer.spy_daily`; record actual first and last cached dates, bar count, and missing-session count or report any live download blocker. Smoke-check `/spy` with the resulting cache.
- [ ] Commit the SPY routes, assets, tests, and usage instructions; do not commit downloaded data.
