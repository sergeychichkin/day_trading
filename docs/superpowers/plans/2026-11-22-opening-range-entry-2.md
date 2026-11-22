# Opening-Range Entry 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add Entry 2, replace a pending buy stop with a strictly lower trigger, and compare separate and combined entry runs while permitting sequential intraday trades.

**Architecture:** Evaluate entry conditions in the same chronological minute loop that handles fills and exits. The backtest runner shares one verified cache snapshot across independently funded scenarios. The standalone HTML report stores chart data once and selects scenario-specific trades, order history, equity and drawdown.

**Tech Stack:** Python 3.13, TOML, SQLite, pytest, Playwright and the existing Lightweight Charts 4.1.1 bundle.

**Spec:** [Approved Entry 2 design](../specs/2026-11-22-opening-range-entry-2-design.md)

## Global Constraints

- Work in the current checkout on main. Keep the existing uncommitted strategy, configuration, and report work. Stage only named task files.
- Use verified regular-session one-minute bars, America/New_York time, signal-close Wilder ATR(20), and the chart's session VWAP estimate.
- Keep one working buy stop while flat and one position at a time. Several sequential fills per session are allowed.
- Use the current $100,000 starting balance, 4× buying power, default 0.25% risk per trade, and current modeled fees and slippage.
- Use inclusive dates with calendar 2020 as the default. Protect the original HTML baseline at output/QQQ_opening_range_2020-01-01_2020-12-31.html.
- Keep the existing 12 Executed Trades Log headings and their order.

## Review Focus

- A working order triggers during its prospective replacement's candle. Attribute the fill to the existing order.
- A trigger gaps beyond all buying power. Clear that order and consider a fresh signal at the close.
- A morning loss is recovered by a later trade on the same day. Include the loss in closed-balance drawdown.
- Two fills share a date and have different setups or stop paths. Clicking each trade selects its own chart details.
- The day ends at 13:00 and lacks a bar labeled 13:00. Apply any protective stop during the final minute and use the final close for scheduled liquidation.

## Files and Responsibilities

- qqq_viewer/opening_range.py and tests/test_opening_range.py own entry detection, order precedence, per-trade execution and the session event trace.
- qqq_viewer/backtest_opening_range.py, config/QQQ.toml and tests/test_opening_range_runner.py own settings migration, scenario selection, independent account balances, verified inputs and statistics.
- qqq_viewer/static/opening-range.html, opening-range.js, opening-range.css and tests/browser/test_opening_range_report.py own comparison and chart navigation. Preserve the existing standalone report writer interface in qqq_viewer/opening_range_report.py.
- README.md documents the QQQ configuration, CLI, and resulting report.

## Task 1 — Chronological session replay

**Files:** qqq_viewer/opening_range.py, tests/test_opening_range.py.

**Interfaces:** Add EntrySettings(enabled: bool, entry_lookback: int, stop_lookback: int, entry_atr: float, stop_atr: float, risk_pct: float). Add entry1 and entry2 properties to Settings as EntrySettings objects, with shared account, trail, tick and cost inputs. replay_session(session: Session, bars: list[Bar], atrs: list[float | None], equity: float, cfg: Settings, daily_ratio: float | None, slippage_multiplier: float) -> SessionReplay. SessionReplay supplies trades: list[Trade], events: list[OrderEvent] and ending_balance: float. Add entry_type: str and order_id: int to Trade. OrderEvent supplies time, action, entry_type, order_id, signal_time, entry_trigger, initial_stop, shares, prior_order_id and reason. Use entry1 and entry2 as stable IDs and session-local, increasing order IDs. Use placed, replaced, filled, canceled and skipped as event actions. Event time is the effective timestamp, so a placement or replacement is timestamped one minute after signal_time and a noon expiration is timestamped 12:00. For a replacement, order_id identifies the new order and prior_order_id identifies the displaced order. Unavailable prices and IDs are null. A report key combines date and order ID.

- [ ] Write fixture tests for the new replay with the current synthetic bar helper. Pin these assertions.

      assert entry2_first_event.signal_time == session.open_utc + 4 * 60
      assert entry2_first_event.time == session.open_utc + 5 * 60
      assert long_bull_run_candidate.signal_time == first_bear_time
      assert doji_sequence.events == []
      assert noon_candidate.reason == "no_active_window"
      assert filled_order.order_id == working_order.order_id
      assert replacement.entry_trigger < earlier.entry_trigger
      assert replacement.prior_order_id == earlier.order_id
      assert replacement.initial_stop == expected_signal_stop
      assert replacement.shares == expected_recalculated_shares
      assert equal_and_higher_events_only_skip
      assert gap_clears_trigger_and_close_can_place_fresh_order
      assert stop_activation_time == qualifying_bear_time + 60
      assert second.entry_time > first.exit_time
      assert early_exit.exit_time == short_session.close_utc
      assert final_minute_stop_precedes_early_close_exit

- [ ] Run .venv/Scripts/python.exe -m pytest tests/test_opening_range.py -q and confirm failure for the missing replay interface.
- [ ] Implement replay_session. Track one working order and one position. Start each minute by handling prior orders and stops. At 12:00 cancel the pending order before examining the bar. At 13:00 liquidate at the open. For a session closing by 13:00, process the final minute's stop first and liquidate any remaining shares at the final close. At each eligible close, manage a surviving position's trailing stop or evaluate the enabled entries if flat. Round prices as in the existing engine and resize eligible lower-trigger replacements from their own settings. Cap shares at both trigger and actual modeled fill. Clear an unaffordable triggered order before evaluating the completed candle. Log ties, higher triggers, invalid or unaffordable candidates, and noon candidates. Preserve gap references, adverse ordering and MFE rules. Charge costs at exit and carry the realized balance into every subsequent order. Keep the current simulate_day path available until the runner migrates in Task 2.
- [ ] Run .venv/Scripts/python.exe -m pytest tests/test_opening_range.py -q and confirm the replay and existing execution checks pass. Commit the scoped engine and tests on main.

## Task 2 — Settings, scenarios and realized statistics

**Files:** qqq_viewer/opening_range.py, qqq_viewer/backtest_opening_range.py, config/QQQ.toml, tests/test_opening_range.py, tests/test_opening_range_runner.py.

**Interfaces:** Keep load_settings(path: Path) -> tuple[str, Path, Settings], run(ticker: str, store: Store, settings: Settings, start: date, end: date) -> dict, and the existing CLI. The run payload contains ticker, start, end, settings, months, benchmark, benchmark_pct, bars, input_bars, input_sha256 and scenarios. Each scenario has id, label, entries, slippage_multiplier, trades, events, equity, stats and trades_without_daily_atr. Use entry1-base, entry2-base, both-base and corresponding -stress IDs, with base runs followed by stress runs. Equity and benchmark points contain integer time (UTC seconds) and value. Every serialized trade and event includes its New York date. The common months map keeps the existing day date, close, range_high, range_low and bars arrays. Scenario data supplies all signal and order annotations.

- [ ] Write tests for flat and nested TOML, two same-day exits, and output protection. Use a small verified SQLite fixture and distinguish per-exit balance from final close.

      assert (legacy.entry1.enabled, legacy.entry2.enabled) == (True, False)
      assert legacy.entry1.risk_pct == 0.5
      assert nested.entry2.risk_pct == 0.125
      assert scenario_ids == ["entry1-base", "entry2-base", "both-base",
                              "entry1-stress", "entry2-stress", "both-stress"]
      assert entry2_only_ids == ["entry2-base", "entry2-stress"]
      assert both_disabled_config_is_rejected
      assert two_same_day_exits_have_distinct_equity_points
      assert stats(trades, [98_000, 101_000, 101_000], 100_000)["max_drawdown_pct"] == 2.0
      assert default_path.name == "QQQ_opening_range_entries_2020-01-01_2020-12-31.html"
      assert explicit_legacy_path_is_rejected_before_writing

- [ ] Run .venv/Scripts/python.exe -m pytest tests/test_opening_range_runner.py -q and confirm the missing behavior.
- [ ] Migrate QQQ TOML to [strategy], [entry1] and [entry2]. Preserve flat-file risk, lookbacks and offsets as Entry 1 settings, with Entry 2 disabled. Validate flags, field types, positive account and risk constraints, and warmup coverage. Build scenarios only for enabled entries. Calculate indicator inputs once per session, run replay_session per scenario, and keep each balance from its SessionReplay. Use an initial equity point at the first session open, after-exit points, and session-close points. Deduplicate a close at the exact same timestamp as a realized exit by keeping the ending balance. Use the same epoch timeline for the price-only benchmark and preserve the existing cache fingerprint. Derive drawdown from every realized balance. Set the default output path and reject writing to the original QQQ baseline. Migrate the original engine tests to replay_session, retaining fee, gap, MFE and stop assertions. Remove the superseded simulate_day path once the CLI uses the new engine.
- [ ] Run .venv/Scripts/python.exe -m pytest tests/test_opening_range.py tests/test_opening_range_runner.py -q and confirm all assertions. Commit the scoped runner, tests and configuration on main.

## Task 3 — Comparison report and chart links

**Files:** qqq_viewer/static/opening-range.html, qqq_viewer/static/opening-range.css, qqq_viewer/static/opening-range.js, qqq_viewer/opening_range_report.py if required by the new payload, tests/browser/test_opening_range_report.py.

**Interfaces:** Keep write_report(path: Path, data: dict) -> None. The browser consumes Task 2's scenarios, trades and events. Keep the monthly candles and VWAP in the common months payload. Select both-base by default when present, otherwise the enabled entry's base run. Key chart and table selections by scenario ID, date and order ID.

- [ ] Add a Playwright test using a generated fixture report with both-base selected, two trades on one day, a replacement across entry types, and an unfilled order that expires. Pin these assertions.

      assert report_table_columns == [
          "Number", "Date", "Entry Time", "Entry Price", "Exit Time", "Exit Price",
          "Shares", "PnL", "Comm", "Slippage", "ATR/Close %", "MFE %",
      ]
      assert initially_selected_scenario == "both-base"
      assert same_day_second_selection_shows_its_own_signal_and_initial_stop
      assert replaced_order_segment_ends_when_replacement_takes_effect
      assert expired_order_segment_ends_at_noon
      assert stress_selection_refreshes_equity_and_trades
      assert not browser_errors

- [ ] Run .venv/Scripts/python.exe -m pytest tests/browser/test_opening_range_report.py -q and confirm the old report lacks scenario selection or same-day trade identity.
- [ ] Add a comparison table and run selector. Display selected-run equity, drawdown, trade rows and order markers. Draw each accepted signal and initial-stop segment from the signal to the actual fill, effective replacement, or cancellation. Keep the active protective stop path tied to the clicked trade. Show the entry type in markers or selected detail and allow inspecting replaced and expired orders. Clear chart selection when changing runs and retain sort/filter and month navigation. Label the new Entry 1-only run's lifecycle difference from the archived baseline.
- [ ] Run .venv/Scripts/python.exe -m pytest tests/browser/test_opening_range_report.py -q and confirm clean interactions. Commit the scoped report files and test on main.

## Task 4 — Run and reconcile 2020

**Files:** README.md. Generated reports stay inside the existing ignored output directory.

- [ ] Record the baseline hash with Get-FileHash -Algorithm SHA256 output/QQQ_opening_range_2020-01-01_2020-12-31.html. Expect 56f73f4805be15ce055d20a0a0f1f4261b5077a015cc5eedeb37b1c55f3d131d.
- [ ] Run .venv/Scripts/python.exe -m qqq_viewer.backtest_opening_range. Expect the distinct _entries_ HTML output with six cases and 98,310 bars across 253 regular sessions. Independently reconcile each scenario's trade PnL to realized ending balance, fills to order IDs, and every day's sequence to the no-overlap invariant. Verify same-day balance points and per-exit drawdown.
- [ ] Run a bounded --start 2020-02-03 --end 2020-02-07 smoke check with each entry disabled in turn using temporary copies of the ticker config. Expect only that enabled entry's base and stress runs.
- [ ] Open the real comparison in Edge via Playwright. Switch entry combinations and slippage assumptions. Verify trade-row zoom, including two trades on one date when present, and compare marked stops and replacement windows to embedded events.
- [ ] Run .venv/Scripts/python.exe -m pytest -q, git diff --check and the baseline hash check again. Require passing tests and the exact original SHA-256.
- [ ] Document per-ticker entry switches and the report invocation in README.md. Commit scoped documentation on main. Report each 2020 scenario's trades, return, costs and maximum closed-balance drawdown.
