# Entry 3 RID Breakout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backtest Entry 3 with the exact RID bands displayed by the report.

**Architecture:** A pure Python session-level RID calculation returns both causal prior upper bounds and completed chart segments. Replay consumes the bounds, and the runner serializes the same segments for the browser.

**Tech Stack:** Python 3.13, TOML, standalone HTML/JavaScript, pytest, Playwright.

**Spec:** [Entry 3: RID breakout below VWAP](../specs/2026-11-22-entry-3-rid-breakout-design.md)

## Global Constraints

- Work on `main`, without a worktree or a push.
- Use completed one-minute candles **09:34 through 11:59** New York time, inclusive.
- Breakout requires `close > open`, `close > prior RID upper band`, and `high < candle VWAP` using unrounded VWAP.
- The first later strict bear consumes the setup; re-entry and dojis do not cancel it. A fresh breakout is required after consumption; reset at each session.
- Buy lookback is fixed at four preceding candles plus signal. Stop lookback defaults to four preceding candles plus signal. ATR(20) multipliers default to `0.25`.
- Preserve existing order activation, noon cutoff, sizing, order replacement, exits, and Entry 1/2 priority. No broker orders or dependencies.
- Entry 3 is disabled when `[entry3]` is absent. Keep old Entry 1/2 cases when Entry 3 is enabled.

## Review Focus

- A 25-bar rollover, a wick crossing the upper band, or a close exactly on the boundary must not arm a setup.
- A doji back inside the old range must leave the setup armed until the first strict bear.
- A skipped signal cannot be reused after a competing entry, open position, missing ATR, or noon cutoff.
- Entry 1/2-only cases must not silently inherit Entry 3 from the enabled configuration.
- The browser must render the supplied RID segments without reconstructing them, and must not label Entry 3 as Entry 2.

### Task 1: Compute RID and replay Entry 3

**Files:** Create `qqq_viewer/rid.py`; modify `qqq_viewer/opening_range.py`; test in `tests/test_opening_range.py`.

**Interfaces:** `calculate_rid(bars: Sequence[Bar]) -> RidSession` returns
`prior_upper: tuple[float | None, ...]` and `segments: tuple[RidSegment, ...]`.
Each segment has `first: int`, `last: int`, `upper: float`, and `lower: float`.
Add `Settings.entry3: EntrySettings` (disabled by default) and a keyword-only
`rid: RidSession | None = None` to `replay_session`. Direct Entry 3 replays
without supplied RID compute it locally.

- [x] Add RID tests for a fresh session with `prior_upper[0] is None`, strict
  inside-range behavior at one cent, a segment covering bars 0–24 but not
  bar 25, and the old upper being available on the candle that starts the
  next segment.
- [x] Add replay tests for wick/equality/VWAP equality and missing VWAP,
  the first eligible 09:34 breakout, doji re-entry followed by a bear,
  repeated qualifying breakouts before the first bear, no cross-session
  carry, a lower-priority or open-position bear consuming the setup, missing
  ATR consumption, and 11:59 `no_active_window`. Assert actual order events,
  including no event at the breakout, next-minute activation, and exact
  tick-rounded levels with default and nondefault ATR/stop lookback.
- [x] Run `.\.venv\Scripts\python.exe -m pytest -q tests/test_opening_range.py`.
  Expect the new tests to fail because RID and Entry 3 do not exist yet.
- [x] Implement `calculate_rid` from the existing chart algorithm, including
  `0.01 + abs(close) * sys.float_info.epsilon` and a maximum of 25 bars per
  segment. Record the previous segment upper before processing each candle.
- [x] Add Entry 3 state to `replay_session`. Track and consume it regardless
  of whether the position and priority rules allow placing an order. Only
  rearm on a later qualifying bullish breakout. Route a winning Entry 3
  signal into the shared order construction and exit logic.
- [x] Re-run the replay test command. Expect new and existing tests to pass.

### Task 2: Wire configuration and report payload

**Files:** Modify `qqq_viewer/backtest_opening_range.py`, `config/QQQ.toml`,
and `tests/test_opening_range_runner.py`.

**Interfaces:** `load_settings` parses optional `[entry3]`; `_scenarios`
retains `entry1`, `entry2`, and `both` IDs and adds `entry3` and `all`.
`run` emits `day.rid_segments` with segment objects from Task 1 and supplies
the `RidSession` to each replay for the day.

- [x] Test absent Entry 3, Entry 3 alone, all three enabled, invalid
  lookback/multipliers, and case isolation. With all three enabled, assert
  20 cases across two rule versions, two slippage multipliers, and five
  entry modes. With Entry 3 disabled, assert the existing 12 cases.
  Assert exact serialized `(first, last, upper, lower)` for a known day.
- [x] Run `.\.venv\Scripts\python.exe -m pytest -q tests/test_opening_range_runner.py`.
  Expect failure for the missing configuration, modes, or segment data.
- [x] Implement config parsing and the five modes, explicitly disabling
  entries outside each mode. Add a configurable, enabled `[entry3]` example
  in `config/QQQ.toml`; leave omission backward compatible. Keep Entry 3's
  trigger lookback at four. Compute one RID result per tested session,
  including when Entry 3 is disabled so chart bands remain available.
- [x] Re-run the runner test command. Expect all tests to pass.

### Task 3: Render and document Entry 3

**Files:** Modify `qqq_viewer/static/opening-range.js`,
`qqq_viewer/static/opening-range.html`, `tests/browser/test_opening_range_report.py`,
and `README.md`.

**Interfaces:** Consume `day.rid_segments`, `scenario.entries`, and
`trade.entry_type === "entry3"` from Task 2.

- [x] Update browser fixtures to supply `rid_segments`. Test that the
  rendered chart follows an explicitly supplied segment, including its
  boundaries; keep the daily reset and visibility tests. Test an Entry 3
  scenario, trade row `3`, chart/selected-trade label `Entry 3`, and
  `#filter` selection showing only Entry 3 trades. Check the filter option is
  absent when no case includes Entry 3 and that existing sorts still work.
- [x] Run `.\.venv\Scripts\python.exe -m pytest -q tests/browser/test_opening_range_report.py`.
  Expect browser failures for the missing Entry 3 UI and unused segments.
- [x] Remove the browser's independent RID calculation and flatten the
  supplied segments for the visible month. Use a single Entry 1/2/3 label
  mapping, add the conditional filter option, and update the report's rules
  description. Preserve default sort and scenario-switch filter behavior.
- [x] Document `[entry3]`, the cutoff, and scenario selection in `README.md`;
  preserve the explicit full-year 2020 command already in the working tree.
- [x] Run `.\.venv\Scripts\python.exe -m pytest -q` and `git diff --check`.
  Expect the complete suite and whitespace check to pass.
