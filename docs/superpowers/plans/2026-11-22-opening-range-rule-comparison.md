# QQQ Opening-Range Rule Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Deliver a single QQQ backtest report with Legacy and Revised rules, sortable entry attribution, and a toggleable RID overlay.

**Architecture:** The runner replays each rules-and-scenario pair independently against one shared cache read. The report selects one pair at a time and draws RID from the shared minute bars.

**Tech Stack:** Python 3.13, SQLite, pytest, JavaScript, Lightweight Charts, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-22-opening-range-rule-comparison-design.md`

## Global Constraints

- Work on the current branch. Keep the cache read-only and preserve both existing 2020 report files.
- Use identical bars, dates, settings, and costs for both versions. Each case starts with its own starting balance.
- Default the report to Revised and keep the scenario selected when the rule version changes.
- Run the Python commands with `.\.venv\Scripts\python.exe` if using the repository's Windows virtual environment.

## Review Focus

- Each Entry 2 high uses its own VWAP, including an equality case and an unavailable VWAP.
- A first bull candle consumes the trailing update even when its candidate cannot raise the stop. A second bull candle needs a fresh bear trigger.
- A stopped trade with a pending update cannot transfer that update to a later trade in the same session.
- A single enabled entry produces only its own base and stress scenarios in both versions.
- RID lines have breaks at range resets and session boundaries, including when a new session starts with the same bounds.

## Task 1. Replay both rule versions

**Files:** `qqq_viewer/opening_range.py`, `tests/test_opening_range.py`.

**Interface:** Define `RuleVersion = Literal["legacy", "revised"]`. Extend `replay_session(..., *, vwaps: list[float | None] | None = None, rule_version: RuleVersion = "legacy") -> SessionReplay`.

- [x] Add `test_revised_entry2_uses_each_candles_own_vwap` with a parameterized four-bull setup followed by a bear or exact doji. Assert a placed event for either high strictly above its own VWAP, and no event for equality on both, both unavailable, or a nonbull candle within the preceding four. Include cases where comparing the prior high against the signal candle's VWAP would produce the wrong result.
- [x] Add `test_revised_trail_waits_for_bull_and_rearms` and `test_revised_trail_pending_state_ends_with_position`. Assert unchanged stops on the arming bear, later bears and dojis. Make the confirming bull's low distinct and assert its exclusion, ATR from its close, activation at `bull.time + 60`, and a second update after a new bear. Include a nonraising candidate that consumes the wait, and an exit before confirmation followed by a fresh fill whose first bullish close cannot update the stop.
- [x] Run `python -m pytest tests/test_opening_range.py -q`. Confirm the new cases fail with the missing rule parameter.
- [x] Keep the existing Legacy branches. In Revised, apply the Entry 2 VWAP comparisons before order creation and store the trailing wait on each `Position`. Keep stop fills ahead of completed-candle processing. Use `bars[i - cfg.trail_lookback:i]` for the Revised candidate and keep the current inclusive slice for Legacy.
- [x] Rerun `python -m pytest tests/test_opening_range.py -q`. Verify all tests pass and commit the replay and its tests together.

## Task 2. Run and identify both versions

**Files:** `qqq_viewer/backtest_opening_range.py`, `tests/test_opening_range_runner.py`.

**Interface:** Keep `run(ticker: str, store: Store, settings: Settings, start: date, end: date) -> dict`. Each `scenarios` record has a unique version-prefixed `id`, a shared `scenario_id` such as `both-base`, and a `rule_version` of `legacy` or `revised`.

- [x] Add runner assertions for unique pair IDs and identical scenario sets in both versions. With a single enabled entry, assert two cases per version and one starting equity point per case. Verify the new default filename and that output path validation rejects the previous comparison report as well as the older baseline and cache paths.
- [x] Run `python -m pytest tests/test_opening_range_runner.py -q` and verify the new assertions fail against the existing payload and output naming.
- [x] Expand `_scenarios(cfg: Settings) -> list[tuple[dict, Settings]]` across both rule versions and pass the version to replay inside the existing session loop. Preserve the shared market data, benchmark, and digest. Use `<ticker>_opening_range_rule_comparison_<start>_<end>.html` as the default and protect the prior comparison path for that ticker and date range using the existing `_same_path` check.
- [x] Rerun `python -m pytest tests/test_opening_range_runner.py -q`. Verify all tests pass and commit the runner and its tests together.

## Task 3. Render the comparison report

**Files:** `qqq_viewer/static/opening-range.html`, `qqq_viewer/static/opening-range.js`, `tests/browser/test_opening_range_report.py`.

**Interface:** Use the runner's scenario records and the existing `[time, open, high, low, close, volume, vwap]` bar tuples. Add `#rules-select` and `#toggle-rid` and retain `#run-select` for scenario IDs.

- [x] Expand the browser fixture with distinguishable results for both rule versions and trades of both entry types. Assert default Revised, switching to Legacy while keeping the stress scenario and selected month, a refreshed order list and summary, and Entry values sorting `1`, `2` and then `2`, `1`. Assert that no-trade rows span the headers and PnL styling follows the PnL column.
- [x] Add a browser fixture for RID with a range lasting 25 bars, an exact tolerance-bound close, and consecutive sessions that share bounds. Assert that the overlay starts from the first bar of each session, resets on the expected bars, and has breaks at the resets and the session boundary. Verify the visibility toggle across a rule switch.
- [x] Run `python -m pytest tests/browser/test_opening_range_report.py -q`. Confirm the new UI checks fail before implementation.
- [x] Resolve the active case by `rule_version` and `scenario_id` and refresh all run-dependent views on either selector change. Render the scenario table for the selected version. Add an `entry_type` sort field and show its one-digit label beside Date. Identify the PnL cell by its field and include version and scenario in run provenance.
- [x] Implement `ridBands(days)` in `opening-range.js` to return the upper and lower line data with explicit gaps. To keep the number of chart series bounded, alternate between two upper/lower pairs and insert whitespace points into the inactive pair, switching pairs at both session starts and range resets. Start a range at bar count 1, hold it while count is below 25 and `close < upper - 0.01 && close > lower + 0.01`, and reset it otherwise. Apply `#90a4ae` to all four line series and preserve toggle state as displayed data changes.
- [x] Rerun `python -m pytest tests/browser/test_opening_range_report.py -q`. Verify all checks pass and commit the report and its tests together.

## Task 4. Verify the 2020 report

**Files:** `README.md` and the generated report in `output/`.

- [x] Record the two existing 2020 reports' SHA-256 values. Run `python -m pytest -q` and `git diff --check`. Verify the full suite and whitespace check pass.
- [x] Run `python -m qqq_viewer.backtest_opening_range` using the repository's read-only QQQ cache. Verify 12 cases and that the generated report opens with Revised and Both entries · Base selected.
- [x] Compare matching Legacy cases against the previous Entry 1 / Entry 2 report's embedded data. Confirm identical canonical input hash, trade ledgers, order events, equity, and statistics. If the input digest differs, run the pre-change runner from commit `cb78415` against the same cache using a temporary source directory and fresh output path, then use that result as the parity reference. Recheck both old report SHA-256 values and verify their bytes are unchanged.
- [x] Update the README's report description and opening path. Commit the documentation and provide the generated report path and verification result.

## Execution notes

- Task 1 `88e70b0`. The existing settings allow a zero trailing lookback. Revised consumes the bull confirmation and keeps the active stop in this case because there are no preceding lows to use.
- Task 2 `7f384c0`. Output validation also protects the archived 2020 comparison when the current invocation uses different dates.
- Task 3 `c24b825`. The bundled chart library drops whitespace from line-series data, so RID uses the existing canvas primitive interface to draw disconnected segments. Browser checks include the chart pixels with the overlay on and off.
- The independent review found a floating-point edge case at exactly one cent. Regression cases for both bounds failed before the fix and passed afterward. The comparison allows for binary roundoff while preserving sub-cent prices.
- The 2020 run covered 98,310 bars in 253 sessions. All six Legacy cases matched the archived comparison exactly, both archived file hashes matched, and the combined report passed the Edge smoke check. The full suite passed all 64 tests.
