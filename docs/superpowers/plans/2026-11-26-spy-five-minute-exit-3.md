# SPY Five-Minute Exit 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add the overnight Exit 3 protective stop to the SPY five-minute backtester and deliver the revised standalone chart and trade log.

**Architecture:** Extend the session replay with optional carried-position state and reuse the existing Entry 1 order path and fill accounting. The runner resolves prior daily levels and builds the report payload. The standalone report renders the payload with the bundled Lightweight Charts library.

**Tech Stack:** Python 3.13, TOML, SQLite snapshot reader, pytest, Playwright, JavaScript and Lightweight Charts.

**Spec:** `docs/superpowers/specs/2026-11-26-spy-five-minute-exit-3-design.md`

## Global Constraints

- Work on the current branch. Use verified inputs read-only and fresh paths for reports. Keep QQQ, acquisition, and the separate SPY viewers outside the change.
- Keep one open position per Exit 3 scenario and the existing one-setup-per-session behavior. Entry signal starts default to New York `09:30` through `09:40`, inclusive.
- Exit 3 uses a fixed `0.5 × ATR(20)` offset, rounded down to the configured price tick. A higher stop takes effect on the next bar.
- The shipped TOML enables Exit 3 and disables Exits 1 and 2. Old configurations retain the legacy parser defaults when their exit keys are omitted.
- Both report ATR overlays default to off. Their configurable multipliers default to `0.25` for high and `0.5` for low.

## Review Focus

- A final-bar adjustment survives a weekend and can be gapped through before the next entry signal. Pin this in Task 1.
- A partial fill bar can contain a low from before entry or a high from after a stop. Pin this in Task 1.
- The immediately prior daily level can be missing, on a mismatched basis, or outside intraday coverage. Pin this in Task 2.
- A cutoff can follow a qualifying final candle with a stop pending and a legacy scenario consuming a next-open tail. Pin this in Task 2.
- A chart jump can target the second month already loaded, and a rolling volume average must agree across overlapping displays. Pin these in Task 3.

---

### Task 1 — Carry and manage one position in the session replay

**Files:** Modify `qqq_viewer/spy_5m_replay.py`. Extend `tests/test_spy_5m_replay.py` and reuse its synthetic bar fixture.

**Interfaces:** Add `"trailing_stop"` to `ExitMode`. Extend `replay_day(..., *, next_open=None, position: OpenPosition | None = None, prior_lowers: tuple[float | None, float | None] = (None, None), next_bar_open: int | None = None) -> DayResult`. Append `position: OpenPosition | None = None` to `DayResult`. Give `Trade` the fields `mfe_r: float`, `mae_r: float` and `stop_adjustments: tuple[StopAdjustment, ...]`. Each `StopAdjustment` carries `candle_time: int`, `effective_time: int`, `old_stop: float`, and `new_stop: float`. An `OpenPosition` carries the original setup and fill, active and pending stops, excursion extrema and adjustments. The runner consumes `OpenPosition.entry_time`, `entry_price`, `initial_stop`, `active_stop`, `pending_stop`, `shares`, and `stop_adjustments` for the terminal report.

- [x] Extend replay tests using a 100.00 entry fill, 98.00 initial stop, 0.01 tick and known ATRs. Assert that a bullish candle with a 106.00 high and 104.00 low qualifies against prior lowers of 105.00 and 107.00. With ATR 1.01, assert a 103.49 stop from the following bar and no retroactive fill. Assert equality with both levels, a doji, missing ATR, and a lower rounded candidate each leave the stop unchanged.
- [x] Add a Friday closing-bar adjustment and Monday opening gap through it. Assert the carried position uses the adjusted stop at Monday's open, that the exit reference is the open, and that the state becomes flat. Assert a qualifying Monday bar can signal at its close after that exit, while candles closing with the carried position still open produce zero signals. Exercise stopping out a same-session entry and verify it cannot re-arm.
- [x] Add partial-bar excursion assertions. With execution slippage zero, 100.00 fill and 98.00 original stop, an intrabar fill that survives a 104.00 high and 101.00 close yields at least 2.00 R MFE. Its 99.00 low alone contributes no MAE. A same-bar adverse-path stop at 98.00 gives 1.00 R MAE and does not credit a high of unknown order. An opening gap exit at 97.00 yields 1.50 R MAE even if the stop had been raised. Add a legacy next-open case proving the opening fill contributes without using the rest of that session.
- [x] Run `.\.venv\Scripts\python.exe -m pytest -q tests/test_spy_5m_replay.py` and confirm the new assertions fail for the absent behavior.
- [x] Reuse entry detection, order lifetime, affordability, slippage and commission logic. Separate entry and position advancement where needed so a carried stop gets evaluated before a new setup, including when the current day's upper is missing. A carried close can free equity for a fresh order in that session. Allow a day result to return both the carried trade that closed and any new trade or position from the same session, in execution order. Preserve the existing `DayResult.trade` view for legacy callers and expose completed trades through `DayResult.completed_trades: tuple[Trade, ...]`.
- [x] Apply the stop already in force to each bar before looking for a tightening signal. Activate pending stops before the next bar's opening execution. Attribute the update to the qualifying bar and record the effective regular-session timestamp. Compute MFE/MAE using known prices in the holding interval, include modeled fills, and freeze the denominator at simulated entry fill minus initial stop. Use the active stop for exit-gap attribution.
- [x] Run the replay tests again and require both the new cases and existing entry and legacy exit cases to pass. Commit the replay and its tests together.

### Task 2 — Configure and run Exit 3 against verified data

**Files:** Modify `qqq_viewer/backtest_spy_5m.py`, `config/SPY.toml`, and the SPY backtest README section. Extend `tests/test_backtest_spy_5m.py`.

**Interfaces:** Extend `BacktestSettings` with `trailing_stop: bool` and `chart: ChartSettings`. Define `ChartSettings.high_atr_multiplier: float = 0.25` and `low_atr_multiplier: float = 0.5` in the runner module. Use matching keys under `[chart]`. Use `load_settings(path: Path) -> BacktestSettings`. Emit `scenarios[*].open_position` as either null or a record with the Task 1 fields plus `entry_date`, `mark_price`, `mark_time`, and `unrealized_pnl`. Emit `bars[*].atr` and `bars[*].volume_sma_20` as float or null, `settings.chart` with both multipliers, and `prior_lowers` with two dated level and status records on Exit 3 session payloads. Closed trades retain existing records and get the Task 1 metrics and stop adjustments.

- [x] Update defaults and CLI tests. Assert the shipped config selects only `trailing_stop`, fixture TOMLs without the new keys keep legacy exit defaults, CLI flags are rejected, and the runner reads custom grid-aligned entry times and finite nonnegative overlay multipliers from TOML. Keep the all-disabled rejection.
- [x] Add a runner test with NYSE sessions across Thanksgiving and a weekend. Assert the exact two prior dates, that a daily open of 100 and a verified open of 94 suppress the level, and that a missing date does not substitute a third session. Add a valid daily-only date before cache coverage and assert the reported validation status. Run a later `--start` and assert the same dates, levels and rolling volume values on overlapping bars.
- [x] Add a cutoff test with Exit 3 and next-session-open enabled. Assert Exit 3 has zero closed trades for the unfinished position, exposes its last active stop and pending stop, and marks unrealized P&L after entry commission. Assert the legacy case can use its verified tail independently and the tail does not update or close the Exit 3 position. Add a case with a carried stop-out and a same-day completed fresh trade and assert ordered records and correct realized balances.
- [x] Run `.\.venv\Scripts\python.exe -m pytest -q tests/test_backtest_spy_5m.py` and confirm the new expectations fail for the missing configuration or runner behavior.
- [x] Extend the loader, defaults and CLI. Keep legacy omission defaults in the loader and set `trailing_stop` explicitly in the shipped TOML. Remove the two entry-time CLI overrides. Update README usage and make the historical Nov 24 reproduction commands select the legacy exits explicitly.
- [x] Resolve the two exact prior calendar dates for each session. Reuse the existing lower bounds and 5% same-date open guard. Consult earlier daily rows before replay and published coverage as specified. Keep the currently verified chronological input reads and fingerprints. Use the calendar to supply the next regular open timestamp at cutoff without using prices beyond the Exit 3 replay. Maintain independent scenario state and realize all completed trades in timestamp order, including the old position's exit P&L before sizing any new entry. Derive the entry and exit dates from the respective times in New York. Include the open-position mark at the last verified signal-session close.
- [x] Build volume SMA with a 20-item rolling sample through warmup and replay. Attach each bar's existing ATR and SMA to report payloads. Keep VWAP on its existing daily reset. Add adjustment events to the existing event payload and mark the actual execution session of carried exits.
- [x] Run `.\.venv\Scripts\python.exe -m pytest -q tests/test_spy_5m_replay.py tests/test_backtest_spy_5m.py` and require passing results, including the old legacy comparison. Commit the runner, configuration, tests and README together.

### Task 3 — Render the two-month chart and revised trade log

**Files:** Modify `qqq_viewer/static/spy-5m-report.js` and `qqq_viewer/static/spy-5m-report.html`. Extend `tests/browser/test_spy_5m_report.py`. Update SPY report usage in `README.md`.

**Interfaces:** Consume the Task 2 report payload. Use an internal ordered `chartSessions(month)` list for price series, markers, event scope and log jumps. Keep the month selector as the start month, with the actual following calendar month computed across year boundaries.

- [x] Extend browser instrumentation to capture plotted price, volume and line data. Assert the November selection renders November and December together, the last month renders by itself, and a December exit jump from November retains November as the start selector. Assert a December-to-January boundary uses the correct following year and absent months are not skipped.
- [x] Add a test for both overlays initially off and independently switchable. Assert plotted values use the configured multipliers and omit unseeded ATR. Assert volume and SMA share the same overlay scale in the price pane, and the same dated SMA values appear when viewing a month directly or as the next month.
- [x] Add an Exit 3 report test with a multi-session trade and a terminal open position. Assert segments mark each stop at its effective bar, the open position is separate from completed trades, and events span the same two-month window. Assert the three entry-related cells display only `HH:mm`, the exit displays its date, the three removed headings are absent from the Executed Trades Log, and both R metrics render. Confirm the empty row spans the updated column count.
- [x] Run `.\.venv\Scripts\python.exe -m pytest -q tests/browser/test_spy_5m_report.py` and confirm the new assertions fail for the absent report changes.
- [x] Load the two calendar months into one price chart. Reuse the existing volume overlay scale pattern. Draw the rolling volume line, optional ATR lines and effective stop segments. Keep the checkbox and chart window state when jumping within loaded sessions. Present pending adjustments and terminal open P&L in the selected scenario. Update the executed-trade headings, formatting and navigation while retaining audit fields in the underlying data.
- [x] Run `.\.venv\Scripts\python.exe -m pytest -q` and require a passing suite with the SPY browser tests exercised. Then run a bounded real-data replay using `.\.venv\Scripts\python.exe -m qqq_viewer.backtest_spy_5m --config config/SPY.toml --start 2025-09-01 --end 2025-12-31 --output <new-report-path>`. If needed, use a longer bounded range to see an actual adjustment. Open the generated report, verify chart month navigation, overlays, logs and position reporting, and record the effective range, input fingerprints and trade or adjustment counts. Preserve any existing report. Commit the report changes and tests.
