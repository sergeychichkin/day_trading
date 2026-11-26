# Comprehensive Review: `qqq_viewer.backtest_spy_5m`

**Repository snapshot:** `sergeychichkin/day_trading` (`826c36ae387eb37a351c92494d0028c19f6911c8`)  
**Scope:** `qqq_viewer.backtest_spy_5m` CLI/runner, replay engine, standalone HTML report generator and frontend assets, cache/snapshot/daily data boundaries, configuration, test suite, and all associated documentation.

## Executive Summary

`qqq_viewer.backtest_spy_5m` is a carefully engineered offline backtesting command for the SPY five-minute Entry 1 strategy. Its core design gets the hardest quantitative and data-integrity concerns right:

- **Causal execution:** Wilder ATR(20) warms deterministically from `published_start` (independent of `--start`), daily level checks inspect only opening prices (never same-day high/low/close), and orders activate strictly on the next bar with exclusive expiration.
- **Exact accounting:** Position sizing, realized P&L, and compounded cash balances use `Decimal` throughout (`spy_5m_replay.py` and `backtest_spy_5m.py`), preventing floating-point drift at whole-share boundaries.
- **Mathematically sound intrabar ambiguity:** Same-bar entry-and-stop ambiguity is flagged only when `entry_bar.open < trigger and bar.close > stop`, correctly recognizing that a bar closing at or below the stop guarantees a stop-out after any intrabar trigger.
- **Read-only isolation & provenance:** SQLite inputs are copied with WAL verification into a temporary directory via `read_snapshot()`, report output uses exclusive creation (`open("x")`) plus path/hard-link/Windows-ADS protection, and both input SHA-256 fingerprints are embedded in the HTML payload.

Even with that strong foundation, this review identified **10 bugs/defects**, **7 testing/analytics/architectural gaps**, and **5 documentation-vs-code inconsistencies**. None appear to corrupt realized P&L under the default `config/SPY.toml` run, but several affect chart accuracy, CLI ergonomics, test coverage, and doc reliability.

### Findings Summary

| Category | High | Medium | Low | Total |
| :--- | ---: | ---: | ---: | ---: |
| Bugs & Functional Defects | 1 | 4 | 5 | 10 |
| Testing, Reporting & Architectural Gaps | 1 | 4 | 2 | 7 |
| Documentation vs. Code Inconsistencies | 0 | 3 | 2 | 5 |
| **Total** | **2** | **11** | **9** | **22** |

---

## Scope and Component Map

| Layer | Files Reviewed |
| :--- | :--- |
| CLI & Runner | `qqq_viewer/backtest_spy_5m.py`, `config/SPY.toml` |
| Replay Engine & Shared Math | `qqq_viewer/spy_5m_replay.py`, `qqq_viewer/opening_range.py` (`WilderATR`, `commission`, `order_price`) |
| Report Generator & Frontend | `qqq_viewer/spy_5m_report.py`, `qqq_viewer/static/spy-5m-report.html`, `qqq_viewer/static/spy-5m-report.js`, `qqq_viewer/static/opening-range.css` |
| Data & Storage Boundaries | `qqq_viewer/spy_5m_snapshot.py`, `qqq_viewer/spy_5m_store.py`, `qqq_viewer/spy_5m_contiguous.py`, `qqq_viewer/spy_5m_view.py`, `qqq_viewer/spy_daily.py`, `qqq_viewer/sessions.py` |
| Tests | `tests/test_backtest_spy_5m.py`, `tests/test_spy_5m_replay.py`, `tests/browser/test_spy_5m_report.py` |
| Documentation | `README.md`, `EGM.md`, `qqq_viewer/EGM.md`, `qqq_viewer/static/EGM.md`, `docs/spy-5m-coverage.md`, `docs/superpowers/specs/2026-11-25-spy-five-minute-backtest-design.md`, `docs/superpowers/plans/2026-11-25-spy-five-minute-backtest.md`, `pyproject.toml` |

---

## Bugs and Functional Defects

### B1 (High — Frontend Charting): Same-bar stop-outs render an invisible protective-stop line

- **Location:** `qqq_viewer/static/spy-5m-report.js:70-75`, `156-157`
- **Root Cause:** `drawMonth()` plots the dashed protective stop via `horizontal(trade.entry_time, Math.min(trade.exit_time, session.close_utc - STEP), trade.initial_stop, ...)`. When a trade is stopped out on the same 5-minute candle it entered (`trade.entry_time === trade.exit_time`) — which includes **every** ambiguous trade — the loop produces a single-point `LineSeries` (`[{time, value}]`). Because `line()` passes `...quiet` (`priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false`), TradingView Lightweight Charts 4.1.1 draws no line segment for a 1-point series.
- **Impact:** On same-bar stop-outs, the dashed red protective stop advertised in the chart legend is completely invisible. (`test_stop_and_ambiguity_have_distinct_chart_markers` checks markers only, so this went undetected.)
- **Recommendation:** For single-bar trades (`trade.entry_time === end`), either extend the horizontal segment by one step (or across the session/signal-to-exit span) or render a price line / marker-backed segment so the stop level is visibly drawn.

### B2 (Medium — CLI/UX & Performance): `--output` is validated only after running the full backtest

- **Location:** `qqq_viewer/backtest_spy_5m.py:386-391`
- **Root Cause:** `main()` executes `data = run(settings, args.start, args.end)` before calling `output_path(args.output, settings, data["coverage"])`.
- **Impact:** If the user passes an existing `--output` path, a non-`.html` extension, an invalid Windows ADS path, or a protected input path, the CLI copies the entire SQLite database, verifies and hashes all ~252,450 bars across 13 years, and runs the full simulation before failing with a `ValueError`.
- **Recommendation:** When `args.output` is provided, validate `output_path()` (and the config-overwrite check) *before* calling `run()`. Keep the post-`run()` check only for the default filename that depends on `data["coverage"]` (and retain `open("x")` in `write_report()` for TOCTOU safety).

### B3 (Medium — Config Parsing): Native TOML local-time values are rejected

- **Location:** `qqq_viewer/backtest_spy_5m.py:63-68`
- **Root Cause:** `_settings_entry()` unconditionally calls `time.fromisoformat(values[key])`. In TOML, unquoted local times such as `entry1_start_time = 09:30:00` are valid syntax that `tomllib.load()` parses directly into `datetime.time` objects, causing `time.fromisoformat()` to raise `TypeError` and be rejected as invalid.
- **Impact:** Users writing valid native TOML time literals in a custom config file receive a misleading error (`entry1_start_time must be a five-minute New York candle time.`).
- **Recommendation:** Accept `isinstance(values[key], time)` directly before falling back to `time.fromisoformat()` for strings, while still enforcing the 5-minute grid, zero-second, and naive-time checks.

### B4 (Medium — Frontend Accuracy): Sub-cent `slippage_floor` is rounded in the report description

- **Location:** `qqq_viewer/static/spy-5m-report.js:17, 259-260`
- **Root Cause:** `#execution` formats `account.slippage_floor` using `money()`, which uses `Intl.NumberFormat("en-US", {style: "currency", currency: "USD"})` (2 decimal places).
- **Impact:** If a user configures `slippage_floor = 0.005` (half a cent), the simulation charges `$0.005`, but the report text claims `"Slippage is at least $0.01"`.
- **Recommendation:** Format `slippage_floor` with up to 4 decimal places (e.g., `$${price(account.slippage_floor)}`).

### B5 (Medium — Frontend/CSS Mismatch): Misleading sort cursors and non-functional row selection highlight

- **Location:** `qqq_viewer/static/opening-range.css:20-21`, `qqq_viewer/static/spy-5m-report.js:196-213`
- **Root Cause:** `spy-5m-report.html` reuses `opening-range.css`, which sets `#trades th { cursor: pointer; }` and styles `#trades tbody tr[aria-selected="true"]`. Unlike `opening-range.js`, `spy-5m-report.js` attaches no sort handlers to `#trades th` and never sets `aria-selected="true"` on clicked rows.
- **Impact:** Column headers appear clickable/sortable but do nothing, and clicking a trade row provides no persistent visual highlight in the log.
- **Recommendation:** Either override `#trades th { cursor: default; }` in `spy-5m-report.html` (or add column sorting) and toggle `aria-selected="true"` on the active trade row in `jumpTrade()`.

### B6 (Low–Medium — Diagnostics): Normal end-of-cache boundary is reported as an "Incomplete grid" error

- **Location:** `qqq_viewer/backtest_spy_5m.py:281-287`, `qqq_viewer/static/spy-5m-report.js:257`
- **Root Cause:** When `next_session_open = true` and `signal_days[-1]` is the last published session, `run()` calls `_verified_bars(db, candidate)` for the following unpublished NYSE date. Because no rows exist for that date, `_verified_bars()` raises `ValueError("Incomplete SPY five-minute grid on YYYY-MM-DD.")`, which is stored verbatim in `coverage["next_open_tail_unavailable"]` and rendered in `#coverage`.
- **Impact:** Users running `next_session_open = true` through the end of their cache see `"Next open unavailable: Incomplete SPY five-minute grid on 2026-11-25."`, which looks like database corruption rather than a normal unpublished boundary.
- **Recommendation:** Distinguish between "no rows for candidate date beyond published cache" (e.g., `"No verified session in cache after 2026-11-24"`) and genuinely damaged/conflicting tail data.

### B7 (Low–Medium — Robustness): Uncaught `IndexError` possible in next-session lookup

- **Location:** `qqq_viewer/backtest_spy_5m.py:283`
- **Root Cause:** `candidate = sessions(signal_end + timedelta(days=1), signal_end + timedelta(days=14))[0]` indexes `[0]` without checking whether the returned list is empty, and `main()` only catches `(ValueError, OSError, sqlite3.Error)`.
- **Impact:** If `signal_end` reaches the end of `pandas_market_calendars`'s NYSE calendar table (or a >14-day closure), the process crashes with an unhandled traceback instead of a clean error or `next_open_tail_unavailable`.
- **Recommendation:** Check if the list is empty and record `tail_error = "Next NYSE session is outside the exchange calendar."`.

### B8 (Low — CLI Formatting): Negative net P&L prints with misplaced dollar sign

- **Location:** `qqq_viewer/backtest_spy_5m.py:397`
- **Root Cause:** `f"... net P&L ${stats['net_pnl']:,.2f}."` places `$` before the formatted number.
- **Impact:** Negative P&L prints as `$-1,234.56` instead of `-$1,234.56`.
- **Recommendation:** Format sign before currency symbol (e.g., `f"{'-' if pnl < 0 else ''}${abs(pnl):,.2f}"`).

### B9 (Low — UI Consistency): Unformatted order shares and leaked `exit_only` month under `same_day_close`

- **Location:** `qqq_viewer/static/spy-5m-report.js:109`, `239-240`
- **Root Cause:**
  1. `drawEvents()` renders `event.shares ?? "—"` without `count.format(...)`, whereas `drawTradeLog()` uses `count.format(trade.shares)`. A 1,250-share order displays as `1250` in Order Events and `1,250` in Executed Trades.
  2. When both exit modes are enabled and `next_open_tail` falls in a new calendar month (e.g., Dec 1 after a Nov 28 `--end`), that exit-only month remains in `#month` even when the user switches `#scenario` to `same_day_close`.
- **Recommendation:** Format `event.shares != null ? count.format(event.shares) : "—"`, and either hide or annotate exit-only months when `same_day_close` is active.

### B10 (Low — Dead / Duplicated Code): Unreachable config-overwrite check and duplicated `publication()` helper

- **Location:** `qqq_viewer/backtest_spy_5m.py:266-270`, `389-390`
- **Root Cause:**
  1. On line 389, `path.exists() and path.samefile(args.config)` is unreachable because `output_path()` already raises if `candidate.exists()` (line 369) or if `candidate.suffix.lower() != ".html"` (line 367).
  2. Lines 266–270 reimplement `spy_5m_store.publication(db)` inline without its `end < start` guard (causing corrupted `published_start > published_end` metadata to raise a confusing `"Select a replay range within published SPY coverage..."` error instead of `"Invalid published SPY coverage bounds."`).
- **Recommendation:** Reuse `spy_5m_store.publication(db)` directly and move the `args.config` protection into `output_path()` alongside the cache protection (before the `candidate.exists()` check).

---

## Gaps (Testing, Reporting, Analytics, and Architecture)

### G1 (High — Test Coverage): Zero test coverage for CLI entrypoint and window-override flags

- **Evidence:** Across `tests/test_backtest_spy_5m.py`, `tests/test_spy_5m_replay.py`, and `tests/browser/test_spy_5m_report.py`, `backtest_spy_5m.main()` is called **0 times**. Neither `--entry1-start-time` / `--entry1-end-time` CLI arguments nor `load_settings(..., entry1_start_time=..., entry1_end_time=...)` keyword overrides are tested.
- **Impact:** Regressions in `argparse` flags, CLI override precedence over TOML values, stdout summary formatting, stderr error handling, or exit codes would pass the entire test suite undetected.
- **Recommendation:** Add unit tests in `tests/test_backtest_spy_5m.py` exercising `main()` for: happy-path default output, `--entry1-start-time`/`--entry1-end-time` overrides, invalid CLI dates/windows, existing `--output` refusal (exit code 1), and stderr messaging.

### G2 (Medium — Test Coverage): Untested replay and config-validation edge cases

- **Untested branches identified:**
  - `spy_5m_replay.py`: `atr_unavailable` skip (first 19 bars of a fresh cache), `invalid_stop` (`stop <= 0`), `equity <= 0` (`cash_unavailable` entry guard), nonpositive modeled exit price guard (`exit_price <= 0` when `slippage_floor` is huge), and explicit assertion of the `"unaffordable_gap"` skip reason.
  - `backtest_spy_5m.py`: `daily_open_unavailable`, `daily_lower_unavailable`, `slippage_floor < 0`, `slippage_bps >= 10_000`, `tick_size <= 0`, `entry_atr_buffer < 0`, `stop_atr_buffer < 0`, `account_exact` serialization in `_wire()`, and `AccountSettings` mixed `float`/`Decimal` types when keys are omitted from TOML.
- **Recommendation:** Add targeted parametrized tests covering each skip reason and config validation rule.

### G3 (Medium — Reporting Observability): Computed fields serialized into HTML but omitted from the UI

- **Dropped fields:**
  - `Trade.bear_time`: Recorded by `spy_5m_replay.py:48` and serialized in `PAYLOAD`, but omitted from the Executed Trades Log columns and not marked on the price chart (users see `Signal`, `Buy`, and `Exit`, but not which bear candle fixed the trigger/stop).
  - `coverage.volume_unit`: Stored in `data["coverage"]["volume_unit"]` (`backtest_spy_5m.py:350`), but displayed neither in `#coverage` nor in `#provenance` (which only dumps `{settings, fingerprints}`), and omitted from the chart legend's `Vol ...` readout.
  - `event.date`: Attached to every event dict (`backtest_spy_5m.py:328`), but unused by `spy-5m-report.js` (which derives dates via `dateAt(event.time)`).
- **Recommendation:** Add a `Bear candle` column (or chart marker) for `bear_time` and surface `volume_unit` in `#coverage` / `#provenance` so volume numbers are unambiguous.

### G4 (Medium — Backtest Analytics): Limited performance metrics and unlinked charts

- **Location:** `qqq_viewer/backtest_spy_5m.py:197-211`, `qqq_viewer/static/spy-5m-report.js:44-52, 225-232`
- **Gap:** `_stats()` reports only `trades`, `ending_equity`, `net_pnl`, `return_pct`, `max_drawdown_pct`, `win_rate`, `ambiguous`, and cost totals. Standard evaluation metrics — profit factor, average win / average loss, expectancy, CAGR, Sharpe/Sortino, exit-reason breakdown (`protective_stop` vs. time exit), and exposure — are absent. In the UI, `#equity` and `#drawdown` do not synchronize crosshairs or zoom ranges, and the benchmark has no drawdown series or summary return KPI.
- **Recommendation:** Add exit-reason counts, profit factor, avg win/loss, and benchmark return to `_stats()` / the scenario table, and sync time scales between `#equity` and `#drawdown`.

### G5 (Low–Medium — Cache Verification Parity): Verification differences vs. `spy_5m_contiguous`

- **Location:** `qqq_viewer/backtest_spy_5m.py:166-186, 262-290`, `qqq_viewer/spy_5m_snapshot.py:29`
- **Gap:**
  - `backtest_spy_5m.run()` does not run `PRAGMA quick_check` (unlike `spy_5m_contiguous.verify_published`), and it allows reading a verified `next_open_tail` session *beyond* `published_end` (`test_verified_next_open_beyond_published_signal_cutoff_can_close_existing_trade`), whereas `spy_5m_contiguous.verify_published()` rejects any cache where `MAX(date) != published_end`.
  - `read_snapshot()` checks `st_nlink > 1` on the main `.sqlite3` file to block hard-link aliases, but does not check `st_nlink > 1` on `-wal`.
  - `backtest_spy_5m.py` passes `FiveMinuteBar` into `WilderATR.update(self, bar: Bar)`, which works by duck typing but violates its static type annotation.
- **Recommendation:** Document the intentional tail-beyond-`published_end` behavior or align both verifiers; optionally add `st_nlink > 1` check for `-wal` in `read_snapshot()`.

### G6 (Low — CLI Ergonomics): Default output filename ignores entry window and exit modes

- **Location:** `qqq_viewer/backtest_spy_5m.py:356`
- **Gap:** The default output path `output/SPY_5m_entry1_<start>_<end>.html` does not encode `--entry1-start-time`, `--entry1-end-time`, or enabled exit modes. Any parameter sweep without `--output` immediately collides with the first run.
- **Recommendation:** Either keep the baseline name only for default settings or append window/exit suffixes when overridden.

### G7 (Low — Multi-Ticker Extensibility): Hardcoded SPY assumptions vs. spec's "first adapter" goal

- **Location:** `qqq_viewer/backtest_spy_5m.py:26, 28, 92, 243, 264, 356`
- **Gap:** The design spec frames SPY as "the first adapter" under a per-ticker TOML layout, but `SPY_CONTRACT`, `DAILY_IDENTITY`, `LEGACY_SPY_DAILY_SHA256`, and output filename prefixes are hardcoded inline. Additionally, the price-only benchmark and unadjusted daily/intraday join assume no stock splits (valid for SPY, but a prerequisite to address before supporting split-adjusted tickers).

---

## Documentation vs. Code Inconsistencies

| ID | Document | Documented Statement | Actual Code Behavior | Severity |
| :--- | :--- | :--- | :--- | :--- |
| **D1** | `docs/superpowers/plans/2026-11-25-spy-five-minute-backtest.md` (lines 36, 38–72) | Lists `load_settings(path: Path)`, CLI without `--entry1-start-time`/`--entry1-end-time`, `months` without `exit_only`, `equity: float`, and leaves all 20+ task checkboxes unchecked (`- [ ]`). | `load_settings()` accepts `entry1_start_time` and `entry1_end_time` kwargs; CLI exposes `--entry1-start-time` and `--entry1-end-time`; `months` includes `exit_only`; `replay_day()` accepts `equity: float \| Decimal` and returns `Trade.pnl: Decimal`; all 3 tasks are implemented. | Medium |
| **D2** | `docs/superpowers/specs/2026-11-25-spy-five-minute-backtest-design.md` (lines 15, 16, 29) | Does not mention `--entry1-start-time`/`--entry1-end-time` CLI overrides, the `bar.close > stop` ambiguity condition, the hardcoded level ratio bounds (`1 <= upper/open <= 2`, `0.5 <= lower/open <= 1`), or `LEGACY_SPY_DAILY_SHA256` allowlisting. | All four are implemented and enforced in `backtest_spy_5m.py` and `spy_5m_replay.py` (the `bar.close > stop` refinement is mathematically superior to the spec's wording and should be back-ported into the spec). | Medium |
| **D3** | `README.md` (line 177) vs. `spy-5m-report.js` (line 254) | `README.md` states: *"the intraday hash covers the replay bars, not the SQLite file bytes"*, while `spy-5m-report.js` displays `signal_bars` as *"replay bars"* separately from *"ATR warmup bars"*. | `intraday_sha256` in `backtest_spy_5m.py:295-298` hashes **all** consumed bars: `warmup_bars + signal_bars + optional next_open_tail bars`. Enabling `next_session_open` on a sub-range (`--end < cache_end`) changes `intraday_sha256` relative to a `same_day_close`-only run on the same dates. | Medium |
| **D4** | `EGM.md` (line 15), `qqq_viewer/EGM.md` (lines 1–63), `qqq_viewer/static/EGM.md` (lines 5–7) | `EGM.md` lists `config/QQQ.toml` but omits `config/SPY.toml`. `qqq_viewer/EGM.md` omits `spy_5m_snapshot.py` entirely and keeps a QQQ-only title/Overview/Invariants/Side Effects structure. `qqq_viewer/static/EGM.md` omits the SPY 5m report from its Overview and does not document the shared `opening-range.css` dependency. | `config/SPY.toml` and `qqq_viewer/spy_5m_snapshot.py` are active components; `spy_5m_report.py:25` embeds `opening-range.css` directly into the SPY 5m report. | Low |
| **D5** | `qqq_viewer/backtest_spy_5m.py:150-163` vs. `qqq_viewer/spy_5m_view.py:67-93` | Both modules validate daily levels against intraday open, but use different status taxonomies (`daily_upper_unavailable` lumps together missing daily rows, warmup `None`, malformed values, and ratio > 2× in `backtest_spy_5m.py`, whereas `spy_5m_view.py` distinguishes `no_daily_row`, `invalid_levels`, and `missing_levels`). | Undocumented divergence between the viewer and backtest level-status labels; makes cross-checking skipped sessions between `/spy/5m` and the backtest report harder. | Low |

---

## Prioritized Remediation Plan

1. **Quick Wins (High Value, Low Effort):**
   - Fix **B1** in `spy-5m-report.js` so single-bar stop-outs render a visible protective stop line.
   - Fix **B2** in `backtest_spy_5m.py` by validating explicit `--output` paths before running `run()`, and clean up **B8** (negative P&L sign), **B9** (`event.shares` formatting), and **B10** (reuse `spy_5m_store.publication()`).
   - Fix **B4** (`slippage_floor` precision in `#execution`) and **B5** (`#trades th` cursor + `aria-selected` row highlight).
   - Add **G1** CLI tests for `backtest_spy_5m.main()` and `--entry1-start-time`/`--entry1-end-time`.
2. **Medium-Term Improvements:**
   - Fix **B3** (native TOML `time` support), **B6** (clearer end-of-cache tail message), and **B7** (`IndexError` guard).
   - Address **G2** (remaining replay/validation unit tests) and **G3** (surface `bear_time` and `volume_unit` in the report UI).
   - Reconcile **D1–D5** across `README.md`, `EGM.md` files, and the design/plan docs so the documentation matches the implemented CLI, ambiguity rules, level bounds, and fingerprint scope.
3. **Optional Enhancements:**
   - Expand `_stats()` and chart interactivity (**G4**: profit factor, exit-reason breakdown, synced equity/drawdown crosshairs).
   - Extract a ticker adapter interface (**G7**) before adding a second 5-minute instrument.
