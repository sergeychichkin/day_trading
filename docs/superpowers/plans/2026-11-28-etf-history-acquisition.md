# ETF Daily and Five-Minute History Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task by task.

**Goal:** Collect and audit Yahoo daily and TWS five-minute history for the 23 approved ETFs, then publish the latest uninterrupted verified block for each eligible symbol.

**Architecture:** A run directory freezes the symbol list, dates and provider options. An append-only request archive is the evidence base. Verification derives per-session outcomes, audit ledgers and replay caches from that archive, with atomic publication of each verified symbol bundle.

**Tech stack:** Python 3.13, `yfinance`, `ib_async`, the existing NYSE calendar and SQLite history schema, JSON, CSV and pytest.

**Spec:** `docs/superpowers/specs/2026-11-28-etf-history-acquisition-design.md`

## Global constraints

- Work on `main` in the current checkout. Preserve existing QQQ and SPY data, configs and reports. Write new data beneath `data/etf_history/<run-id>/`.
- Use the exact 23-ticker roster in the design, including `PFF`. The request floor is `2007-01-01`. The default cutoff is the latest completed NYSE session at run creation, recorded once and reused on resume.
- Use Yahoo daily with `auto_adjust=False`, `actions=True` and regular-session TWS native `5 mins` `TRADES` with UTC timestamps. Record routing, instrument identity, volume unit and input hashes.
- Preserve raw responses, failures and anomalies. Downloads continue past internal gaps. Build verified replay inputs only from independently complete sessions with traceable source attempts.

## Review focus

- A truncated or timed-out request containing all timestamps cannot certify a session. A later individually completed response may certify the date, subject to conflicts and validation.
- An identical timestamp returned with different prices or volume stays in the conflict ledger. The archive also preserves duplicates, invalid values and off-grid rows.
- The expected early-close grid, NYSE holidays and DST shifts must reconcile. Unknown early history and confirmed pre-listing sessions have distinct statuses.
- Resuming after a crash between raw-response write and checkpoint must reuse the archived evidence and keep deterministic canonical output. A changed cutoff, symbol identity or provider option cannot silently resume the same run.
- A gap can split a verified block in the middle or truncate the latest one. Publication selects the block with the latest end. Daily files with missing rows retain their signal eligibility disclosures. A replay or integrity failure leaves the release unpublished.

### Task 1. Run configuration, evidence and Yahoo daily acquisition

**Files:** `equity_backtest/acquire_history.py`, `equity_backtest/acquisition_archive.py`, `equity_backtest/daily_history.py`, `qqq_viewer/spy_daily.py` as needed for shared indicator math, and new acquisition tests.

- [ ] Add fixture tests for the exact roster, cutoff on a weekend or pre-close weekday, a later listing, duplicated or invalid Yahoo rows, a missing eligible session resetting indicators, and a restart after an archived response but before checkpoint. Assert output files are independent of existing SPY and QQQ paths.
- [ ] Run the new test file before implementation and confirm the new entry points or assertions fail.
- [ ] Define `python -m equity_backtest.acquire_history --run-dir <new-run-path> --port 7497 --volume-unit shares`. Support `--resume` on the same directory and optional `--through` for an earlier completed session; compare all frozen manifest parameters when resuming. Use the existing local TWS default port and expose it explicitly. Support separate daily, intraday and offline audit/publish stages under the same run manifest.
- [ ] Archive immutable provider response records per request attempt with a stable request key, attempt number, parameter fingerprint, received rows, error metadata and hash. Use atomic file writes. A checkpoint is committed only after its evidence is durable. Resume reconciles records left by an interrupted checkpoint and reuses terminal request outcomes.
- [ ] Generalize the existing daily indicator builder with a ticker input and reuse its formulas. Preserve the SPY downloader's entry point and behavior. Request each Yahoo symbol individually with explicit settings and an exclusive API end of cutoff plus one day. Retain corporate actions, adjusted close and unusable raw rows; emit symbol-identified canonical JSON and per-session daily status. Discover verified first-trading evidence where needed, recording the source and keeping unknown dates distinct.
- [ ] Re-run the daily and archive tests plus the existing SPY daily tests.

### Task 2. TWS acquisition and per-session verification

**Files:** `equity_backtest/tws_history.py`, the acquisition runner and archive, `qqq_viewer/spy_5m_backfill.py` if shared request mechanics move, and TWS fixture tests.

- [ ] Build a fake transport for unique and ambiguous contracts, requested end/duration checks, monthly partial and empty responses, per-day recovery, access denial, pacing, timeout with rows, disconnected transport and overlapping responses. Assert every eligible window is attempted or has an explicit unresolved disposition. Check half-day, DST and off-grid rows against real calendar fixtures.
- [ ] Run the TWS fixture tests before exposing the generic transport.
- [ ] Generalize contract qualification to an exact USD common stock-class or ETF `STK` match from the SMART request. Record conId, primary exchange, currency and routing. Require explicit disambiguation or record an unresolved symbol if multiple exchange/contract identities remain. An optional head timestamp supplies context in the manifest and cannot certify historical coverage or listing.
- [ ] Reuse the existing historical request lifecycle with qualified ticker, native RTH bars, explicit UTC, existing conservative serial pacing and bounded timeouts. Archive responses before canonicalization, including rows with unusable timestamps, and record provider codes. Use one calendar-month chunk anchored at the last targeted session close, check the requested coverage, and split shorter if needed. Retry eligible missing or incomplete days individually with bounded backoff. Recover transient connectivity where possible and preserve every unattempted window as such when provider access cannot resume.
- [ ] Verify each canonical session against its expected grid, including required usable price/volume/WAP and a completed full-session attempt. Record conflicting valid provider variants and trace their attempt IDs. Freeze first accepted values as evidence and withhold the session from verification when a conflict remains. Audit every date through the frozen cutoff even after a TWS gap. Re-run the fake-transport and existing SPY acquisition tests.

### Task 3. Audit and atomic replay publication

**Files:** `equity_backtest/history_audit.py`, the acquisition runner, `docs/five-minute-inputs.md`, `README.md`, and audit and publication tests.

- [ ] Add an end-to-end fixture with confirmed pre-listing dates, a daily gap, an internal intraday gap, a trailing empty session, an unresolved request, a broad price-basis discrepancy and at least two verified blocks. Assert exact UTC and New York missing-minute timestamps, disjoint session dispositions, separate daily counts and the latest block's exact published bounds. Test missing daily release inputs, a symbol with no verified block, and an interrupted release.
- [ ] Run the audit fixture tests and observe the new checks fail before implementing them.
- [ ] Generate `audit/symbols.csv`, `audit/sessions.csv`, `audit/missing_5m_bars.csv`, `audit/anomalies.csv` and `audit/verified_blocks.csv`. Key records by requested symbol, date and source or interval where applicable. Include expected slots and observed/canonical/verified distinctions, attempt or row identifiers, status reason and evidence. Record missing daily sessions in the session ledger. Attribute confirmed pre-listing periods only from recorded evidence.
- [ ] Apply the existing common-factor open/close price-basis test to comparable sessions. Publish no block through a corroborated unresolved basis mismatch. Detect suspected split and distribution dates using provider actions and residuals, flag the evidence and keep prices intact. Enumerate all verified blocks and select the one with the latest end. Record every excluded session and the cutoff shortfall.
- [ ] Stage `<run-dir>/replay/<TICKER>/` with `intraday.sqlite3`, `daily.json` and `config.toml`. Include the verified endpoints, ticker, conId and volume unit. Verify integrity and every intraday session from a read-only snapshot, and smoke-run the existing generic offline backtester. Publish the whole staged directory atomically after verification. Write manifest statuses and artifact hashes for published, withheld and empty outcomes.
- [ ] Update acquisition, resume and replay instructions. Re-run audit, report and acquisition tests and the full suite. Confirm retained SPY and QQQ input hashes are unchanged.

### Task 4. Live acquisition and reconciliation

- [ ] Start the new run with the latest completed session cutoff. Check a bounded Yahoo and TWS sample including a liquid ETF and a newer ETF. Confirm the qualified contracts, observed fields, calendar alignment, returned timestamps, configured volume unit and pacing before releasing the full request queue.
- [ ] Run the complete Yahoo download and paced TWS acquisition, monitor progress, recover transient failures and resume the same run. Distinguish terminal provider evidence from request problems. Continue all work that can proceed for other symbols when an individual symbol is blocked.
- [ ] Run the offline audit and publication stages. Independently reconcile expected dates, verified blocks and missing-bar totals for all 23 symbols. Check all staged bundle identities and endpoints through the generic runner. Compare retained input hashes again.
- [ ] Write the dated coverage report with a row for every symbol, all published bounds and shortfalls, total daily and five-minute gaps, provider failures and direct links to detailed ledgers and configs. Run `git diff --check` and confirm every documented command and artifact path exists.
