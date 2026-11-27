# SPY Five-Minute Entry 1 Daily Lower Stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use the entry session's plotted Lower as the Entry 1 initial stop and measure same-session recovery after protective stop-outs.

**Architecture:** The runner passes the same validated Lower to the chart and the replay engine. The replay engine rounds that price to a tradable tick and sizes new orders against it. A comparison run uses the pre-change revision and the revised runner with matched inputs.

**Tech Stack:** Python 3.13, pytest, local SPY SQLite and daily JSON caches, standalone HTML and Playwright.

**Spec:** [Approved daily Lower stop design](../specs/2026-11-27-spy-five-minute-entry-1-daily-lower-stop-design.md)

## Global Constraints

- Work on the current branch. Preserve the verified caches and use their published history.
- Use the same validated Lower the report plots and round it down to the configured tick. Apply the rule across all three exit scenarios.
- Preserve the entry trigger, order timing, costs, fills, sizing rules and exit logic. Apply the change to new orders. Manage carried Exit 3 positions before evaluating a new entry.
- Preserve existing config compatibility and make the effective stop rule clear in the report.

## Review Focus

- An off-tick Lower and a later intraday low must yield the rounded Lower, with shares based on that rounded stop. Task 1 tests both.
- A missing or invalid daily Lower must be logged as unavailable. A rejected opening-price match or invalid rounded stop must retain its specific reason. Task 1 tests the replay and runner paths.
- An unavailable current Lower must leave carried-position management available. Task 1 tests protective exits for carried positions.
- Older TOML files must load with an explicit migration notice, while the report describes the actual stop. Task 1 tests both paths.
- A recovery on the stop-out bar or next session must be excluded, and a last-bar stop must have an unknown recovery outcome. Task 2 checks these cases before processing real trades.

## Task 1 — Change Entry 1 sizing and stop submission

**Files:** `qqq_viewer/spy_5m_replay.py`, `qqq_viewer/backtest_spy_5m.py`, `qqq_viewer/static/spy-5m-report.js`, `config/SPY.toml`, `README.md`, `tests/test_spy_5m_replay.py`, `tests/test_backtest_spy_5m.py`, and `tests/browser/test_spy_5m_report.py`.

**Interfaces:** Add keyword-only `session_lower: float | None` to `replay_day(...) -> DayResult`. The runner supplies `chart_session["lower"]` from its existing `_levels` validation. Keep the `Trade.initial_stop` and event payload fields. Remove `EntrySettings.stop_atr_buffer` from the effective settings.

- [ ] Add failing assertions before the production change. In `test_spy_5m_replay.py`, set the lower to `97.509` and assert `(trade.entry_trigger, trade.initial_stop, trade.shares) == (102.5, 97.5, 200)` across exit modes using a valid next open for that mode. Change a pre-submission candle's low and assert the same values. For `None`, assert `skip_reason == "daily_lower_unavailable"`; for a price equal to the trigger or a price that rounds to zero, assert `skip_reason == "invalid_stop"`. In each rejected case assert no order submission. Add a carried Exit 3 fixture with `session_lower=None` and a stop-hit bar, and assert it still completes a `protective_stop` trade.
- [ ] In `test_backtest_spy_5m.py`, assert an otherwise tradable session with stored Lower `98.009` reports `initial_stop == 98.0`. With Lower missing or above the daily open, assert `skips["daily_lower_unavailable"] == 1`. Keep the existing opening-price mismatch test. Assert that a legacy TOML `stop_atr_buffer = 0.25` emits `UserWarning` containing `stop_atr_buffer` and loads effective settings without that field. In the report browser tests, assert `#execution` describes the daily Lower as the initial stop and that the trade log displays the rounded stop.
- [ ] Run `.venv/Scripts/python.exe -m pytest -q tests/test_spy_5m_replay.py tests/test_backtest_spy_5m.py` and confirm the new assertions fail against the old behavior.
- [ ] Pass the validated Lower into `replay_day`. At the existing bear-close submission point, use `order_price(session_lower, account.tick_size, up=False)` and the existing `0 < stop < trigger` check. Emit a rejected event and `daily_lower_unavailable` when the level is missing. Handle nonfinite direct replay inputs as `invalid_stop`. Keep carried-position processing before these entry checks and drop the running `day_low` calculation.
- [ ] Remove the old field from the dataclass and shipped TOML. Permit the legacy key in `load_settings` and discard it with `warnings.warn(..., UserWarning, stacklevel=2)` before constructing `EntrySettings`. Update the report and README with the two effective price rules. Give existing replay fixtures explicit Lower inputs so tests of sizing, gap execution, and exit behavior continue to exercise their intended conditions.
- [ ] Run `.venv/Scripts/python.exe -m pytest -q tests/test_spy_5m_replay.py tests/test_backtest_spy_5m.py tests/browser/test_spy_5m_report.py`. Confirm the calculation, skips, compatibility notice, and report checks pass. Commit the verified scoped changes.

## Task 2 — Compare the old and revised stop rules

**Files:** Existing caches and settings under `data/` and `config/`. Save the comparison evidence and regenerated standalone report under `output/`.

**Interfaces:** Consume `run(settings)` in each version. Use its `fingerprints`, `coverage`, `settings`, `scenarios[].trades`, and `months` payload. Compare matching scenario IDs.

- [ ] Export baseline revision `14680d9c92555dae18526a30fdb78ae82736bc1f` with `git archive` into a temporary directory. Use a temporary config derived from the baseline's `config/SPY.toml` with absolute paths to the same local caches and enable `same_day_close`, `next_session_open`, and `trailing_stop` for both runs. Use the default published date range. Run each version with the repository venv. Record the revision, effective settings, replay dates, and fingerprints. Require matching fingerprints and coverage dates before comparing results.
- [ ] Exercise the comparison calculation on a tiny synthetic tape. Assert that only a later regular-session bar with `high >= entry_trigger` on the stop's `exit_date` counts as a recovery. Assert that a hit on the stop bar alone and a hit the next session are excluded, while a last-bar exit is counted separately as having no later bar.
- [ ] For each exit scenario and rule, report executed trades, median shares, net P&L, total protective-stop exits, eligible stop exits, recoveries, recovery rate, and stop exits with no later bar. Use `exit_reason == "protective_stop"` and look up bars by `exit_date` with `time > exit_time`. Include supporting per-trade recovery flags and skip counts in the comparison evidence. An empty eligible denominator yields an unavailable rate.
- [ ] Run `.venv/Scripts/python.exe -m pytest -q` and `.venv/Scripts/python.exe -m qqq_viewer.backtest_spy_5m`. Verify the default report retains its normal exit configuration and identifies the new stop rule. Link the report and comparison, and state the observed results using the recovery denominator for each scenario.
