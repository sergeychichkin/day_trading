# SPY Five-Minute Shared Stop Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task by task.

**Goal:** Manage one SPY protective stop with Exits 3–5 and report which rule set the stop that closed each trade.

**Architecture:** The runner supplies the current holding day and the prior completed daily session's stop proposals. The replay applies the highest eligible proposal at each effective bar, records the winning source, and uses the active stop's source on a protective-stop closure. The report consumes those fields on the existing shared-position scenario.

**Tech stack:** Python, TOML, pytest, standalone HTML and Lightweight Charts.

**Spec:** `docs/superpowers/specs/2026-11-28-spy-5m-shared-stop-design.md`

## Global constraints

- Work on the current branch. Keep the local input caches intact and use a distinct report path for verification.
- Count holding days from the actual fill using NYSE sessions, with the fill session as day 1. Compute daily signals only from completed consecutive NYSE sessions.
- Use one position, one ratcheting stop, and one equity series for the managed scenario. Keep the two independently compounded legacy comparison modes.

## Review focus

- A qualifying 9:40 candle is ignored. The 9:45 candle can produce a raise at 9:50. An eligible price that fails to raise the stop does not use the daily allowance.
- A proposal on the last bar of day 8 may take effect on day 9. If it wins at the open, it uses day 9's Exit 3 allowance. A last-bar proposal on day 9 cannot take effect on day 10, even across a holiday or weekend.
- A shared opening stop may have candidates from all three rules. A gap through the winning stop uses the existing opening-gap execution convention and the winning source.
- Missing daily bars reset the bear streak and ATR continuity. A fourth bearish candle or repeated red EMA20 does not produce a new trigger.
- A queued proposal superseded at the open does not appear as an effective stop on the chart. The report displays the closure source and preserves sortable loss values and chart controls after the column move.

### Task 1: Replay and source attribution

**Files:** `qqq_viewer/spy_5m_replay.py`, `tests/test_spy_5m_replay.py`.

- [x] Add replay tests for a qualifying 9:40 candle, a non-raising 9:45 candidate, and two higher candidates later in the session. Assert that only the first higher eligible stop takes effect, on the next bar. Test the first allowed day, days 8–10, a configured cutoff, and a gap through the existing stop after the cutoff. Update pre-9:45 fixtures to put Exit 3 triggers at eligible times.
- [x] Add tests with a queued Exit 3 stop and two daily proposals effective at the next open. Assert that prices 103, 105 and 104 produce Exit 4 at 105, an opening gap below it uses the existing gap fill convention, and only one adjustment becomes effective. Exercise equal proposals, a proposal equal to the active stop, and an unadjusted `Initial SL` closure.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/test_spy_5m_replay.py -q` and observe failures for the new rules.
- [x] Add a `StopCandidate` carrying source, trigger time and price. Extend `replay_day` with `holding_day`, `exit3_enabled`, `exit3_max_holding_days` and `opening_stop_candidates`. Keep the existing managed scenario identifier. Derive a holding day from the exchange calendar for direct calls that omit it. Count a same-session Exit 3 raise only when its rounded candidate exceeds the active stop.
- [x] Store queued candidate data together and arbitrate on effective bars before checking that bar's stop hit. Choose the highest candidate above the active stop and use lowest rule number for ties. Record the source on the active position, effective `StopAdjustment` and closed `Trade`, keeping the existing execution reason for fill logic. Retain pending-stop information at cutoff.
- [x] Re-run the replay test file and confirm it passes.

### Task 2: Daily inputs, settings and runner

**Files:** `qqq_viewer/backtest_spy_5m.py`, `config/SPY.toml`, `tests/test_backtest_spy_5m.py`.

- [x] Add a runner fixture with a seeded ATR(20), a three-bear streak, an EMA20 up-to-down flip, and known completed daily lows. Assert that the trigger candle is included in the four-session minimum and that changing the next day's OHLC cannot alter that day's opening proposal. Cover the fourth bear candle, gaps in daily history, EMA neutral or missing values, different lookbacks and ATR multipliers, and disabled rules.
- [x] Add a settings and runner test asserting one shared scenario with all three bundled rules enabled. Assert that a legacy TOML containing only `trailing_stop = true` still selects Exit 3 alone, and that a config enabling only Exit 4 or 5 still selects the managed scenario. Verify a new overnight fill is excluded from the just-opened daily raise.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/test_backtest_spy_5m.py -q` and observe failures for the new settings and daily-rule behavior.
- [x] Add independent `exit4` and `exit5` switches to `[exits]`, retain `trailing_stop` for Exit 3, and enable all three in the bundled TOML. Read `[exit3].max_holding_days` and `[exit4]`/`[exit5]` `lookback` and `atr_multiplier`. Default to 9, 4 and 0.25 and validate positive integer day counts and finite nonnegative multipliers.
- [x] Extend the existing validated daily history pass to compute bear streaks, consecutive EMA transitions, rolling lows and proposals using its daily Wilder ATR. Use the previous completed session's proposals only, with per-rule tick flooring. Pass the runner's `session_index` holding age and enabled proposals to `replay_day`. Include the source in report payload events and open positions.
- [x] Run both replay and runner test files and confirm both pass.

### Task 3: Report, docs and verification

**Files:** `qqq_viewer/static/spy-5m-report.html`, `qqq_viewer/static/spy-5m-report.js`, `README.md`, `qqq_viewer/EGM.md`, `tests/browser/test_spy_5m_report.py`.

- [x] Add a browser test for column order and sorting across `Initial SL` and `Exit 3`–`Exit 5`. Assert that an adjustment appears once at the effective bar with its source, that daily-adjustment details show the daily trigger and effective open, and that a superseded raise has no effective chart line. Keep assertions for legacy exit labels and chart jumping.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/browser/test_spy_5m_report.py -q` and observe the new report assertions fail.
- [x] Put **Exit Reason** after **Net P&L**. Display and sort by the stop source for protective exits and use the legacy reason labels for time-based exits. Keep chart controls and stop markers aligned with the new column order. Use daily proposals' effective opening bar for the chart marker and show trigger and effective timestamps in the audit.
- [x] Document the new rules, configuration and labels in the README and runner documentation. Update the legacy-only command guidance so it disables all three managed rules in a copy of the bundled config.
- [x] Run the full pytest suite and `git diff --check`. Generate a current full-cache HTML report to a distinct path. In offline Edge, check the shared scenario, the new column and at least one daily stop adjustment. Report which reasons occurred in the full-cache run and use the synthetic cases to verify any reason absent from it.
