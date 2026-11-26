# SPY five-minute review fixes implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` for inline work, or `superpowers:subagent-driven-development` if the user requests delegation.

**Goal:** Fix confirmed CLI and standalone-report defects without changing the replay.

**Architecture:** Keep the replay engine and extend per-scenario stats with profit factor. Reuse the store's publication validator and distinguish display-only fixes from runner diagnostics.

**Tech stack:** Python 3.13+, pytest, Playwright, bundled Lightweight Charts.

**Spec:** [Approved review-fix design](../specs/2026-11-26-spy-five-minute-review-fixes-design.md).

## Global constraints

- Keep trade rules, sizing, fees, fill prices, and default output filenames.
- Do not download live data, write existing caches, or replace existing reports.
- Preserve the Gemini review and unrelated work.

## Review focus

- A protected path or existing output must fail without calling `run`.
- Quoted and native times use the same validation; CLI values take precedence.
- A missing unpublished tail is normal; a partially present tail is not.
- A stop on the final candle must not extend after that candle or into another session.
- A cross-month next-open exit must remain navigable after switching scenarios.
- Wins without losses, losses without wins, and no nonzero trades must all
  produce meaningful profit-factor displays without nonstandard JSON.

### Task 1: CLI path validation, settings, and tail diagnostics

**Files:** `qqq_viewer/backtest_spy_5m.py`, `tests/test_backtest_spy_5m.py`.

**Interfaces:** Keep `main(argv=None) -> int` and `load_settings(...) -> BacktestSettings`. Extend `output_path` with optional coverage for explicit paths and keyword-only `config_path: Path | None = None`. Keep `coverage["next_open_tail_unavailable"]` as a diagnostic string when no verified tail is available.

- [ ] Add tests: existing output and config aliases fail before a monkeypatched `run` can be called; successful default output and CLI overrides are reflected in the embedded settings; native `09:30:00` works and off-grid times fail; reversed publication uses the store's diagnostic. Pin missing unpublished, malformed, empty-calendar, and verified out-of-range tails separately.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/test_backtest_spy_5m.py -q`; observe failures for the missing behavior.
- [ ] Preflight explicit output and settings paths; keep exclusive writing. Accept native `time` objects before applying the existing common validation. Call `publication(db)`. Only report a normal unpublished boundary when neither bars nor session/attempt/conflict evidence exists for that next NYSE date. If the calendar yields no date, report that separately.
- [ ] Re-run the focused suite; all tests pass.

### Task 2: Offline chart and log accuracy

**Files:** `qqq_viewer/backtest_spy_5m.py`, `qqq_viewer/static/spy-5m-report.js`, `qqq_viewer/static/spy-5m-report.html`, `tests/test_backtest_spy_5m.py`, `tests/browser/test_spy_5m_report.py`.

**Interfaces:** Consume existing `trade.bear_time`, `coverage.volume_unit`, `session.exit_only`, and `settings.account` (including optional `settings.account_exact`). `_stats` adds `profit_factor` per scenario: a finite number, `"infinite"` for wins without losses, or `null` for no nonzero trades.

- [ ] Extend the offline browser fixtures to assert an exact-price point marker on a final-bar stop; a longer stop remains dashed. Assert bear timestamp, visible volume units, `$0.005` slippage floor, non-sortable header cursor, and an exit-only month present only in the next-open scenario. Preserve cross-month exit and event navigation assertions.
- [ ] Add a parameterized stats test for net P&L `[30, -10, 0, -5] → 2.0`, `[30, 0] → "infinite"`, `[-10, 0] → 0.0`, `[0] → None`, and `[] → None`. Assert the rendered summary column shows finite, infinite, and unavailable values across the browser fixtures.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/test_backtest_spy_5m.py -k profit_factor -q`; observe the missing stat fail.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/browser/test_spy_5m_report.py -q`; observe the new assertions fail.
- [ ] Use the bundled line-series point marker option for single-point stops and label it in the chart key. Add the bear candle to the trade log, label chart volume, and display the floor from the exact account value if supplied. Rebuild scenario-specific month choices, preserve a still-valid selection, and omit exit-only sessions in same-day charts even when they share a month.
- [ ] Add profit factor to `_stats()` and the scenario comparison table, computed independently per scenario from realized net trade P&L. Render finite numbers to two decimals, `"infinite"` as `∞`, and `null` as `—`.
- [ ] Re-run the profit-factor runner tests and offline browser suite; all tests pass.

### Task 3: Docs and final verification

**Files:** `README.md`, `docs/superpowers/specs/2026-11-25-spy-five-minute-backtest-design.md`.

- [ ] Document native TOML times and override precedence, fingerprint scope, a verified next-open tail beyond the signal range, and the profit factor definition. Keep the archived input hashes and measured counts.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest -q` and `git diff --check`; expect no failures or whitespace errors.
- [ ] Regenerate both archived-input scenarios to unused temporary filenames. Compare their fingerprints, trade counts, and P&L with the existing verified run; only presentation and normal tail diagnostic text may change.
