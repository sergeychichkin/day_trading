# Executed Trades Timing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show sortable signal clock time and signal-to-entry duration in the Executed Trades Log and let users filter by entry type.

**Architecture:** Derive both sort keys from existing trade timestamps in the browser report. Filter displayed rows by existing entry type before sorting them.

**Tech Stack:** Embedded HTML and JavaScript, pytest, Playwright.

**Spec:** [Executed Trades Log timing](../specs/2026-11-22-executed-trades-timing-design.md)

## Global Constraints

- Keep **Date** separate. Place **Signal Time** before **Entry Time** and **Signal → Entry** after it.
- Display signal time in New York `HH:mm`; do not use the date for sorting or as a tiebreaker.
- Display elapsed minutes as `N min`; sort numerically.
- Replace Winners and Losers with **Entry 1** and **Entry 2**; **All trades** remains the default. Match executed entry type regardless of PnL.
- Preserve existing Entry Time sorting, default order, and row selection. Filtering preserves the active sort and leaves the chart, scenario totals, and order selector unchanged. Do not change replay, execution, or payload data.
- Changes apply to newly generated standalone reports.

## Review Focus

- Winter vs. summer dates: sort by displayed New York time, not UTC clock time.
- Identical local times: preserve fixture order in both sort directions.
- Same-minute fill: show and sort `0 min` correctly.
- Empty scenario: span all 15 columns.
- Existing cells and interaction: preserve PnL styling and trade selection after adding columns.
- Filtering: include losses for the selected entry type; restore all rows in the active sort when switching back.

### Task 1: Add and verify the timing columns and entry filter

**Files:**
- Modify: `qqq_viewer/static/opening-range.html` — table headers and filter options.
- Modify: `qqq_viewer/static/opening-range.js` — table cells, sort keys, and filter predicate.
- Test: `tests/browser/test_opening_range_report.py`.

**Interfaces:** Consume numeric epoch seconds `trade.signal_time` and
`trade.entry_time`, the existing New York `time(timestamp)` formatter, and
`trade.entry_type` (`entry1` or `entry2`). Produce two columns and an entry
filter with no new payload fields.

- [x] **Step 1: Add browser assertions.** Add
  `test_trade_log_sorts_signal_clock_time_and_duration` using a dedicated
  fixture variant with coherent trade dates and timestamps:
  January `10:02` (2 min), July `10:10` (10 min), and July `10:02` (0 min).
  Start in fixture order (January, July `10:10`, July `10:02`).
  Assert that Signal Time ascending orders trade numbers `[1, 3, 2]` and
  descending `[2, 1, 3]`; UTC ordering would put July `10:10` before January
  `10:02`. Assert duration ascending `["0 min", "2 min", "10 min"]` and
  descending `["10 min", "2 min", "0 min"]`. Assert Entry Time ascending
  remains chronological (`[1, 3, 2]`). Use click and Enter across the two
  new sort headers. Update the existing exact header list and empty-state
  colspan assertion, and locate the styled PnL cell by its header index.
  Retain the current row selection and order-window tests.

  Add `test_trade_log_filters_by_entry_type` with trades 1 and 3 as Entry 1
  (trade 3 has negative PnL) and trade 2 as Entry 2. Assert dropdown labels
  `["All trades", "Entry 1", "Entry 2"]` and default value `all`. After
  sorting by Number descending, assert Entry 1 shows `[3, 1]`, Entry 2
  shows `[2]`, and All trades restores `[3, 2, 1]`. Assert the scenario
  summary and order selector options are unchanged.

- [x] **Step 2: Confirm both tests fail before changing the report.**
  Run `.\.venv\Scripts\python.exe -m pytest -q tests/browser/test_opening_range_report.py -k "sorts_signal_clock_time_and_duration or filters_by_entry_type"`.
  Expect failures for missing timing columns and old dropdown options,
  not because setup failed.

- [x] **Step 3: Implement both columns.** Add headers and cells in the approved
  order. Use `time(trade.signal_time)` as the displayed value and lexical sort
  key (zero-padded `HH:mm`), and use `trade.entry_time - trade.signal_time` as
  the numeric duration sort key. Divide by 60 for the displayed minute label.
  Keep `trade.entry_time` as the existing entry sort key. Preserve the shared
  header toggle, keyboard handling, stable sorting and column-index lookups.
  Set dropdown values to `all`, `entry1`, and `entry2`. In `renderTrades`,
  include every row for `all`, otherwise compare the value to `trade.entry_type`.

- [x] **Step 4: Verify the report and regression suite.** Run
  `.\.venv\Scripts\python.exe -m pytest -q tests/browser/test_opening_range_report.py`,
  then `.\.venv\Scripts\python.exe -m pytest -q`. Expect passing tests; report
  any skips or environment limitations separately.
