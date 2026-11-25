# SPY Five-Minute Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backtest one long SPY Entry 1 setup per regular session using the published five-minute cache and exact daily upper level, and produce a QQQ-style report comparing the enabled exits.

**Architecture:** A pure five-minute replay engine handles one session and an optional next opening; a per-ticker TOML runner reads and verifies the local snapshots, warms ATR, and compounds independent scenarios. A separate static report embeds the results and chart assets without changing the QQQ report or SPY viewers.

**Tech Stack:** Python 3.13, `tomllib`, SQLite, existing Wilder ATR and commission helpers, bundled Lightweight Charts, pytest and Playwright.

**Spec:** [SPY five-minute backtest design](../specs/2026-11-25-spy-five-minute-backtest-design.md)

## Global Constraints

- New York candle-start times; default signal window `"09:30"`–`"09:40"`, inclusive. One latch and at most one Entry 1 order per day. A later same-session bear can be outside the signal window.
- Wilder ATR(20) carries across sessions from the published cache start. Both ATR buffers default to `0.25`; the buy stop activates at the next bar and expires after 120 minutes or at the session close, whichever comes first.
- Use the existing daily `upper`; reject opening price ratios differing by more than 5%. Never use that day's high, low or close to select the trade.
- Start separate scenarios at `$100,000`, risk `1%` of current equity in whole shares, and cap buying power at `1×` cash equity by default and at most. Use IBKR Pro Fixed commission and adverse slippage defaulting to `max($0.01, 1 basis point of reference price)`.
- Same-day close enabled and next-session open disabled in the default TOML. Protective stops apply in both runs. Never submit orders or write market-data caches.
- Preserve existing uncommitted SPY daily and five-minute viewer changes. The read-only verified cache and published bounds are prerequisites; keep prior fixes out of backtest commits.

## Review Focus

- A bear at the expiry of the trading day cannot create an executable order; a touch on the exact expiry bar must not fill (Task 1).
- A gap-triggered entry with insufficient buying power must not resize or permit a later setup (Task 1).
- Dark intrabar path ambiguity and overnight stop gaps must use consistent, adverse execution references without double exits (Task 1).
- A different `--start` must preserve ATR on overlapping candles; same-day future price data cannot change entry eligibility (Task 2).
- A next-open exit just past the requested end must remain inspectable, but missing next-open input must not generate a fabricated exit (Tasks 2–3).

---

### Task 1: Implement the session replay

**Files:** Create `qqq_viewer/spy_5m_replay.py` and `tests/test_spy_5m_replay.py`.

**Interfaces:** `EntrySettings` with `entry1_start_time: time`, `entry1_end_time: time`, `entry_atr_buffer: float`, `stop_atr_buffer: float`, `entry_order_valid_minutes: int`; `AccountSettings` with `starting_balance`, `risk_pct`, `cash_cap`, `tick_size`, `slippage_bps`, `slippage_floor`; `ExitMode` values `same_day_close`, `next_session_open`. `replay_day(session: Session, bars: list[FiveMinuteBar], atrs: list[float | None], upper: float | None, equity: float, entry: EntrySettings, account: AccountSettings, exit_mode: ExitMode, *, next_open: tuple[int, float] | None = None) -> DayResult`. `DayResult` has `trade: Trade | None`, `events: list[OrderEvent]`, and `skip_reason: str | None`. Trade records expose `signal_time`, `bear_time`, `entry_time`, `exit_time`, `entry_trigger`, `initial_stop`, `entry_price`, `exit_price`, `shares`, `pnl`, `commission`, `slippage`, `gap_slippage`, `exit_reason`, and `ambiguous`; events expose `time`, `action`, `detail`, `trigger`, `stop`, and `shares` (optional where inapplicable).

- [ ] Add `test_first_bull_and_first_later_bear_own_the_only_setup`: use exact equality, a doji, a 09:40 bull and a 09:45 bear. Assert the 09:40 bar latches, the first bear owns the order, a bear high of `102` with ATR `2` gives trigger `102.50`, and a session low so far of `98` gives stop `97.50`. Assert a later lower low cannot revise the order. Test that 09:45 alone cannot qualify under the default window.
- [ ] Add `test_order_activates_on_next_bar_and_expires_exclusive`: a touch on the bear bar and at exactly activation plus 120 minutes must not fill. Verify an order is valid one bar before expiry, can never survive exchange close, and that a last-bar bear creates no executable order. Assert a canceled, rejected, or stopped setup never re-arms.
- [ ] Add `test_size_and_costs_use_only_submission_inputs`: `$100,000`, a `$5` trigger/stop distance, and sufficient cash give 200 shares; a very tight stop is cash-capped after estimated entry slippage and commission. Assert fixed size, an opening gap through the trigger uses the open plus slippage, and an unaffordable gap rejects rather than resizing. Assert commissions on each executed leg and separate gap/slippage costs.
- [ ] Add `test_stop_at_open_and_ambiguous_entry_bar_have_distinct_fills`: an existing trade gapping below its stop uses the opening price less slippage; an intrabar trigger and stop touch uses the stop reference and marks ambiguity; a triggered opening fill followed by a stop touch does not. Test the scheduled exit after protective stops on a final bar, an exchange early close, and a next-session open gap classified once.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/test_spy_5m_replay.py -q`; verify failures identify absent replay behavior.
- [ ] Implement the pure replay with a one-way setup state machine. Consume the provided Wilder ATR series and reuse existing commission and tick conventions, with decimal-based whole-share risk/cash limits. Anchor unknown intrabar timestamps to the bar start and label them in the event; known time exits occur at the actual exchange close/open. No daily lookup or file writes in this module.
- [ ] Rerun `.\.venv\Scripts\python.exe -m pytest tests/test_spy_5m_replay.py -q`; expect all tests to pass.
- [ ] Commit only the replay module and its tests.

### Task 2: Wire settings, published inputs, and exit scenarios

**Files:** Create `config/SPY.toml`, `qqq_viewer/backtest_spy_5m.py`, and `tests/test_backtest_spy_5m.py`.

**Interfaces:** `load_settings(path: Path) -> BacktestSettings` holds `ticker`, `intraday_cache`, `daily_cache`, `entry: EntrySettings`, `account: AccountSettings`, `same_day_close: bool`, `next_session_open: bool`. `run(settings: BacktestSettings, start: date | None = None, end: date | None = None) -> dict` returns `ticker`, `settings`, `coverage`, `fingerprints`, `scenarios`, `months`, and `benchmark`. A scenario has `id` (the exit mode), `label`, `stats`, `equity`, `trades`, `events`, and `skips`; timestamped equity/benchmark points are `{time, value}`. `months` maps `YYYY-MM` to sessions with `date`, `open_utc`, `close_utc`, `upper`, `lower`, `levels_status`, and `bars` (five-minute OHLCV/WAP/VWAP dictionaries). Trade rows also carry `date` (entry session) and `exit_date`. The CLI is `python -m qqq_viewer.backtest_spy_5m --config config/SPY.toml [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--output path]`.

- [ ] Add `test_config_defaults_and_validation`: assert default window, buffers, 120 minutes, $100,000, 1%, cash-only and exit switches. Resolve input paths relative to the TOML. Reject reversed or off-grid times, invalid duration, non-finite or invalid settings, no enabled exit, and unsupported ticker/cache combinations.
- [ ] Add `test_overlap_keeps_atr_and_ignores_current_daily_high_low_close`: verify alternate start values give the same ATR-based triggers on overlapping bars. Changing a day's final Yahoo high, low or close alone must not change its setup, while a missing upper level or opening mismatch over 5% must skip with a reason. Ensure days before the start warm up without generating trades, and reject gaps, conflicts, or out-of-coverage signal ranges.
- [ ] Add `test_exit_modes_compound_independently_and_use_only_verified_next_open`: both start at `$100,000`; later equity and shares respond only to each scenario's realized P&L. Use the next NYSE opening across a weekend or holiday; when it is just past `--end`, retain its chart session and exit date but take no new signal there. If it is missing, skip that scenario's boundary setup. Verify exact input fingerprints, and protect both input files, SQLite sidecars and aliases from the output path.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/test_backtest_spy_5m.py -q`; verify failures identify absent runner behavior.
- [ ] Implement the runner and TOML. Read the published SPY range, check session evidence and bar grids within the same read transaction used for replay, and snapshot the daily JSON once. Validate the opening-only price basis; do not reuse the chart's close-based check for signals. Warm from the published start, then run the enabled cases independently. Fingerprint the actually consumed intraday fields (including tail-open data) and Yahoo snapshot. Report the cost settings, skipped sessions by reason, realized-equity stats and price-only benchmark. Set the default output to `output/SPY_5m_entry1_<start>_<end>.html`.
- [ ] Rerun `.\.venv\Scripts\python.exe -m pytest tests/test_spy_5m_replay.py tests/test_backtest_spy_5m.py -q`; expect all tests to pass.
- [ ] Commit only the settings, runner, and their tests.

### Task 3: Generate and check the standalone report

**Files:** Create `qqq_viewer/spy_5m_report.py`, `qqq_viewer/static/spy-5m-report.html`, `qqq_viewer/static/spy-5m-report.js`, and `tests/browser/test_spy_5m_report.py`; update `README.md`. Use existing report CSS where it fits, without changing QQQ assets.

**Interfaces:** `write_report(path: Path, data: dict) -> None` consumes the Task 2 result. The runner CLI calls it only after validating the output path; report HTML opens directly from disk with no server or network dependencies.

- [ ] Add `test_report_switches_exit_modes_and_charts_trade_and_tail_exit` with a two-mode example and a next-open exit in a different month. Assert separate summary trade counts/equity, month navigation, daily levels, entry/protective-stop/exit markers, and selecting a trade's exit navigates to the correct next session. Assert the standalone file works with network requests disabled. Test a no-trades scenario and clear labels for simulated ambiguity and costs.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/browser/test_spy_5m_report.py -q`; verify failures identify absent report behavior.
- [ ] Add a standalone HTML report with performance summary, price-only benchmark, equity/drawdown, five-minute chart, order events, and executed trades. Use separate scenario selectors without QQQ-specific rule variants; embed escaped input data and all assets. Document the CLI, TOML toggles, fill assumptions, and how to compare time windows without editing a baseline report.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest -q` and `git diff --check`; expect the full suite and whitespace checks to pass.
- [ ] Run the default configuration on verified data and run both exits enabled using a separate settings file/output. Open the outputs in the browser, confirm reported counts and costs reconcile with row totals, and verify the SPY input and QQQ outputs are unchanged. Record the actual date range, trade counts, and report paths.
- [ ] Commit only the report, its tests, and backtest documentation; do not commit caches or generated HTML.
