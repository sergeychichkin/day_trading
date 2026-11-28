# SPY five-minute Entry 1 VWAP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Apply Entry 1's VWAP, session-high and next-session expiry rules and display the buy-stop lifetime in the SPY report.

**Architecture:** The replay owns one pending buy stop per exit scenario. The runner supplies verified sessions and the same cumulative VWAP used in the chart. The report derives trigger segments from the order audit and clips them to the displayed candles.

**Tech Stack:** Python, pytest, TOML and Lightweight Charts.

**Spec:** `docs/superpowers/specs/2026-11-28-spy-5m-entry1-vwap-design.md`

## Global constraints

- Work on the current branch. Preserve local inputs and use a distinct output path for verification.
- Compare VWAP and the breakout close strictly above the daily Upper. Use completed bars at setup time and keep order terms fixed afterward.
- Use a 9:35 a.m. New York time exclusive expiration on the next NYSE session. Base exits and holding days on the actual fill.

## Review focus

- Confirmation can coincide with either decision candle. Equality or a VWAP reversal at the first post-confirmation bear close ends the setup.
- The high can come from before the breakout and cannot use later bars. An unobserved next opening candle leaves the order pending through cutoff.
- A next-session opening fill can gap past the trigger, hit the initial stop, or close under any of the three exit scenarios.
- A segment crosses a weekend or month boundary with both ends at the observed order timestamps. A cropped chart still shows the portion inside its window.
- Legacy TOMLs can still load when they contain `entry_order_valid_minutes`, with the fixed rule reported in the UI.

## Task 1. Replay and runner

Files `qqq_viewer/spy_5m_replay.py`, `qqq_viewer/backtest_spy_5m.py`, `config/SPY.toml`, `tests/test_spy_5m_replay.py`, and `tests/test_backtest_spy_5m.py`.

- [x] Add tests for VWAP confirmation on the breakout, on an intermediate candle, and on the bear. Assert that VWAP equal to Upper at the first bear after confirmation fails even when a later bear is above Upper. Assert that a known high of 105 and a buffer of 0.50 produce a fixed 105.50 trigger even if a later bar reaches 106. Test that the corresponding Daily Lower and size come from submission.
- [x] Add tests for an order submitted Friday and filled on the next verified session's 9:30 candle. Assert that the same price touch at 9:35 is too late, a cash-cap rejection terminates the order, and no available next opening candle yields a pending order at cutoff. Cover the signal session's closing candle, a still-open Exit 3 position at cutoff, and settlement using each legacy exit mode. Check that a first-following Exit 3 bar cannot raise an overnight entry's original stop and that subsequent sessions count from the fill date.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/test_spy_5m_replay.py tests/test_backtest_spy_5m.py` and confirm behavior tests expose the missing rule changes.
- [x] Extract a cumulative `session_vwaps(bars)` calculation shared by the replay and chart, or pass the chart-calculated aligned values to replay. Reset VWAP at each session. Track the running high across all regular-session bars, including bars before a setup may arm.
- [x] Add an immutable `PendingEntry` with signal and bear times, trigger, stop, shares, buying-power budget, activation and expiry. Accept `pending_entry` and a next NYSE opening timestamp in `replay_day`, and return any unresolved order on `DayResult`. Feed both an overnight pending order and a newly submitted order through the same fill and rejection logic. Use session open plus one `STEP` for expiry and keep the existing open and intrabar fill conventions. Keep the original stop through the first following session after an overnight fill. Continue to permit a new setup in the new session after an old order expires or is rejected or after its position is closed, using the existing re-entry rules.
- [x] Keep pending state by scenario in `run`. Pass the exchange-calendar opening timestamp independently of next-open price availability. Preserve verified-input boundaries, independently compounded balances, and trade metadata keyed to signal or fill as appropriate. Expose an unresolved order separately from `open_position`. Use the selected range to decide entry fills and keep the optional verified tail for next-open exits.
- [x] Remove `entry_order_valid_minutes` from the bundled config. Accept and warn about the obsolete key in existing files. Update affected test fixtures to use valid VWAP and the new duration. Re-run both test files and verify they pass.

## Task 2. Chart and README

Files `qqq_viewer/static/spy-5m-report.js`, `README.md`, and `tests/browser/test_spy_5m_report.py`.

- [x] Add browser coverage for a buy-stop line ending on the fill candle and an unfilled line ending at 9:35 in the following session. Verify that an overnight line renders in both visible sessions, that the signal marker belongs to the signal session, and that switching exit scenarios clears stale lines.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/browser/test_spy_5m_report.py` and confirm the new chart assertions fail before adding the overlays.
- [x] In `drawMonth`, group the active scenario's audit events by submitted order, with explicit order identification if required by the event schema, then use chart timestamps between submission and the applicable end. Use the existing overlay lifecycle, a visually distinct trigger color, and a single-point marker for an interval that hits just one visible candle. Put the signal marker on `signal_time` and the buy marker on `entry_time` when each is visible. Replace the minute-lifetime copy and surface pending orders at cutoff.
- [x] In `README.md`, replace the verbose Entry 1 and Exit 3 paragraphs with compact rules and remove the dated November 24 reproduction section. Preserve the standard replay examples and use the historical coverage record where it already belongs.
- [x] Run the full suite with `.\.venv\Scripts\python.exe -m pytest`. Generate a report to an unused output path using `python -m qqq_viewer.backtest_spy_5m --config config/SPY.toml --output <unused-path>`, check its coverage and order events, and inspect a filled and an expired trigger segment in the browser. Run `git diff --check` and report any outstanding verification limitations.
