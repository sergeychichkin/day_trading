# SPY Five-Minute Exit 3 Session Lookback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkboxes for tracking.

**Goal:** Gate the Exit 3 trailing stop by the NYSE session of the position's entry.

**Architecture:** The runner supplies the immediately preceding session date with the existing newest-first pair of daily lows. The replay decides which slots a carried position can use. The report reads that same decision from the adjustment record.

**Tech Stack:** Python 3.13, pytest, Playwright and the standalone JavaScript report.

**Spec:** [Exit 3 session lookback](../specs/2026-11-26-spy-five-minute-exit-3-session-lookback-design.md)

## Global Constraints

- Work on the current branch and preserve the existing workspace edits. Read market-data caches locally without altering them. Use an unused explicit output filename for verification.
- The entry session permits no stop raise. The first following NYSE session permits only the entry session's `lower`. The second following session and later permit the two immediately preceding levels.
- The active protective stop executes first. A completed bull candle can raise it using `low − 0.5 × ATR(20)` with strict tick flooring and next-bar activation.

## Review Focus

- A fresh position entered after a carried stop-out on the same session uses the entry-session rule.
- Weekends, exchange holidays and early closes leave session order intact.
- A missing entry-session lower does not select the older slot on the first following session.
- From the second following session, either valid slot can qualify, including the older one on its own.
- A first-following-session adjustment reports one prior lookup even when two lookup records exist for charting.

---

### Task 1 — Apply and report the session lookback

**Files:** `qqq_viewer/spy_5m_replay.py`, `qqq_viewer/backtest_spy_5m.py`, `qqq_viewer/static/spy-5m-report.js`, `README.md`, `qqq_viewer/EGM.md`, `qqq_viewer/static/EGM.md`, and the SPY replay, runner, and report tests.

**Interfaces:** Extend `replay_day` with keyword `previous_session_date: date | None = None`. The runner derives the date from the first `prior_lowers` audit row. If a direct call carries a position without the date, use the NYSE calendar to obtain it. Add `prior_session_count: int` to `StopAdjustment` and emit it in the trade and nested event adjustment. The first following session emits 1 and later sessions emit 2. Chart sessions retain both dated lookup rows.

- [x] Extend `tests/test_spy_5m_replay.py`. Use an original stop of `98.00` and a qualifying later entry-day bull candle with low `104.00` and ATR `1.01`. Assert zero adjustments, retention of `98.00`, and a separate entry-day stop fill when price touches `98.00`. Test that a first-following-session candle with a `106.00` high is ineligible for preceding levels `(105.00, 107.00)` or `(None, 107.00)`. From the second following session, the same candle qualifies against `107.00` and yields `103.49`, including when the recent slot is unavailable. Move the existing strict-rounding and stop-ratchet cases to eligible carrying sessions and preserve their exact assertions.
- [x] Extend `tests/test_backtest_spy_5m.py`. Cover a carried trade across Thanksgiving and an early close or weekend. Assert prior-level counts from the replay, retention of the initial stop at the entry cutoff, a pending stop at the cutoff of an eligible later session, and isolation from the legacy exit-only tail. In the carried-exit case, assert a fresh same-session trade cannot tighten its original stop.
- [x] Extend `tests/browser/test_spy_5m_report.py`. Put the demonstrated intraday and weekend stop adjustments on the first regular session after entry. Check that the adjustment row visibly includes the entry day's date, omits the older day's date, and jumps to the correct candle. Keep the two-month and terminal open-position checks.
- [x] Run `.\.venv\Scripts\python.exe -m pytest -q tests/test_spy_5m_replay.py tests/test_backtest_spy_5m.py tests/browser/test_spy_5m_report.py`. Confirm the new expectations fail against current behavior.
- [x] In `replay_day`, use no trailing threshold for a freshly filled position. For a carried position, compare its New York entry date to `previous_session_date`. Use the first slot for a match or the first two slots otherwise, filtering unavailable values after choosing the slots. Pass that threshold and eligible slot count to `_advance`. Keep stop execution before adjustment evaluation.
- [x] In the runner, derive the previous date and pass it with the existing tuple. In the report, render the slice specified by `event.adjustment.prior_session_count`. Update the live rule description and scoped docs with the session table or equivalent wording.
- [x] Run `.\.venv\Scripts\python.exe -m pytest -q`. Require the full suite to pass. Generate a report over a bounded existing SPY range by passing an unused `--output` path. Open the report in the browser and check the rule text and any demonstrated stop event. Confirm the market-data fingerprints are unchanged for the same range.

## Verification

The full suite passed 256 tests. The independent reviewer passed 76 focused tests and found two minor documentation and test gaps. Both were addressed, and the strengthened runner regression passed. The bounded replay from September 2 through December 31, 2025 reproduced the prior input fingerprints, one closed trade and three stop adjustments. The standalone report passed the offline Edge check.
