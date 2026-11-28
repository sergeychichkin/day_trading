# Generic Five-Minute Equity Backtester Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task by task.

**Goal:** Run the existing five-minute replay and standalone chart for verified US stock and ETF caches, and retire the one-minute backtester and both unused intraday viewers.

**Architecture:** Put the offline replay, shared five-minute history primitives, and standalone report in `equity_backtest`. Keep existing provider commands and the separate SPY daily viewer as clients of retained code. Make the ticker and local cache identity explicit at the replay boundary.

**Tech stack:** Python 3.13, TOML, SQLite, pytest, Playwright and Lightweight Charts.

**Spec:** `docs/superpowers/specs/2026-11-28-generic-five-minute-backtester-design.md`

## Global constraints

- Work on `main` in the current checkout. Preserve local inputs, existing reports and the working SPY entry ATR buffer of `0.3`.
- Run verification offline from existing or synthetic data. Use unused explicit paths or private temporary files for new reports during verification.
- Retain the current strategy, NYSE session convention, cache and publication formats, verification and input fingerprints. Leave download and conversion functionality for a separate task.

## Review focus

- SPY files without ticker metadata retain their narrowly scoped compatibility paths. An explicit conflicting ticker fails even when the contract ID matches.
- Stock and ETF configs reject mismatched or absent cache identity, unsafe ticker text and wrong contract IDs. Daily warmup nulls and missing valid rows keep the current signal suppression and reset behavior.
- The SPY publisher and Sierra extender both import price-basis helpers from the deleted viewer. Both still perform the same checks after the extraction.
- The SPY daily server and the offline report must both find Lightweight Charts and their styles after packaging. The daily server must work without a QQQ SQLite file.
- The complete current SPY replay is equivalent across the move. The one-minute QQQ database and any WAL retain their starting hashes.

### Task 1. Establish a read-only baseline and extract shared dependencies

**Files:** `qqq_viewer/opening_range.py`, `qqq_viewer/spy_5m_view.py`, `qqq_viewer/spy_5m_backfill.py`, `qqq_viewer/spy_5m_extend.py`, new `equity_backtest` modules, and affected acquisition and replay tests.

- [x] Record the resolved paths, lengths and SHA-256 hashes of `data/qqq.sqlite3` and its WAL if present without connecting to the file. Record the exact working SPY TOML bytes. Serialize a full-cache baseline using `backtest_spy_5m.run(load_settings(...))` in a private ignored path, recording canonical hashes for trades, events, equity, open and pending state, coverage and both input fingerprints. Use stable per-scenario IDs and canonical sorted JSON serialization. Do not replace existing reports.
- [x] Add shared-helper tests for the current Wilder seed/gap calculation, commissions, and tick rounding at a rounding boundary. Keep the existing five-minute publisher and Sierra price-basis tests. Run tests before moving code and capture the baseline.
- [x] Extract `WilderATR`, `commission` and `order_price` with identical formulas into `equity_backtest.execution`. Update the replay and runner imports. Extract `daily_price_pair`, `price_basis_mismatch` and the daily snapshot row mapping used by the extender into a retained price-basis module. Update both provider consumers. Preserve their legacy imports or wrappers only where an existing command or shared test depends on them.
- [x] Keep acquisition's `FiveMinuteStore`, publication schema, and `read_month` support where required by backfill/contiguous code. Move the provider-independent calendar/bar helpers, publication reader and SQLite snapshot reader used by the generic replay behind generic module paths while keeping SPY downloader compatibility imports. Run the existing five-minute acquisition and replay suites.

### Task 2. Generalize the offline CLI and report

**Files:** `equity_backtest/backtest_5m.py`, `equity_backtest/replay_5m.py`, `equity_backtest/report_5m.py`, report assets, `qqq_viewer/backtest_spy_5m.py`, `pyproject.toml`, `tests/test_backtest_spy_5m.py`, `tests/test_spy_5m_replay.py` and five-minute report browser tests.

- [x] Add config and replay cases for synthetic verified `AAPL` and `IWM` inputs. Assert ticker in the title, the payload and the default output filename. Assert that a wrong intraday contract, missing non-SPY ticker, explicitly conflicting SPY ticker, mismatched daily identity, invalid volume unit and an unverified session raise an error. Reject bool, zero and string `contract_id` values and ticker path separators. Cover legitimate share-class punctuation and daily gaps or warmup nulls. Run the targeted tests against the current SPY-only interface and confirm the new cases fail.
- [x] Move the replay and report writer to their generic modules and make `python -m equity_backtest.backtest_5m` the documented CLI. Carry `ticker` and `contract_id` in `BacktestSettings`. Accept a canonical US ticker containing letters, digits and internal dot or hyphen punctuation. Validate non-SPY ticker and contract in both config and metadata. Keep the existing SPY conId and exact legacy daily fingerprint exceptions scoped to SPY. Match daily snapshot schema and source and keep existing row usability checks.
- [x] Generate the report name from the validated ticker. Keep the current SPY default config, canonical bar fingerprints, default output atomic replacement, explicit output exclusivity, and protection against file and sidecar aliases. Make run and report diagnostics ticker-aware. Retain a thin `qqq_viewer.backtest_spy_5m` CLI forwarder. Point retained tests and internal consumers at the generic module.
- [x] Give the five-minute CSS, HTML and JS generic names. Include `equity_backtest` and its static assets in the built distribution. Keep one vendored chart-library copy with its LICENSE and have both report and SPY daily server load that copy. Run both synthetic normal CLIs, the SPY compatibility CLI and the offline browser smoke tests. Check installed asset paths and exercise both old and new module entry points against the same saved SPY baseline.

### Task 3. Retire the intraday viewers and one-minute backtester

**Files:** `qqq_viewer/server.py`, old strategy and intraday viewer modules, dedicated browser assets, `config/QQQ.toml`, viewer and legacy backtest tests, and active project documentation.

- [x] Add a route test that starts the daily server without a QQQ DB. Assert `/spy`, `/api/spy/daily`, the daily styling and the chart library load. Assert 404s for `/api/months`, `/api/month/<month>` and `/spy/5m` plus its data API, and for the retired JS/assets. Serve `/` as a redirect to `/spy`. Lift the Playwright `page` fixture out of the retiring one-minute viewer test, then run the changed server and report tests.
- [x] Remove the one-minute strategy, runner, report writer, RID strategy helper, `config/QQQ.toml`, viewer routes and assets and dedicated test files. Remove the SPY five-minute viewer, routes and assets once price-basis imports are migrated. Retain acquisition and shared tests. In mixed files such as `tests/test_spy_5m_extend.py`, remove the viewer assertion and keep the modern replay/publisher assertions. Preserve the separate SPY daily server even when the QQQ cache is absent.
- [x] Replace active README and blueprints with the generic command, local input contract and sample config, trade rules, report chart and retained daily viewer. Remove dedicated historical one-minute strategy/viewer and SPY five-minute viewer plans, specs and docs. Keep dated acquisition and verification coverage, plus generic replay history where it remains relevant. Update mixed documents and resolve links to removed files. Keep all pre-existing generated reports.
- [x] Run the complete remaining suite and `git diff --check`. Check package imports, route inventory, documentation links and the final file manifest. Compare SPY settings, trade, event, equity, coverage and fingerprint payloads to the Task 1 baseline. Compare QQQ main and WAL names, sizes and hashes to their Task 1 records. Report the parity and data-preservation results and link to the generic command documentation.

## Verification results

- Full SPY replay parity across 4,670 sessions and 66 closed trades. Canonical payload SHA-256 `1acbcfaabc39c065d7153be8e17325b6231c51f9e393c5437e672a7b9e155487`.
- QQQ one-minute database, durable WAL, both SPY inputs and the working SPY TOML matched their pre-change hashes.
- 218 tests passed. An offline wheel build verified a single bundled chart library and byte-identical output from the two installed CLIs on the same date window. Offline Edge opened the full SPY report and stock and ETF fixture reports.
