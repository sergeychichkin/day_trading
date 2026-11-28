# ETF history download and audit

The acquisition workflow covers QQQ, DIA, XLF, XLK, XLE, XLI, XLP, XLY, XLV, XLU, XLB, IYR, IWM, TLT, TIP, EMB, PFF, IBB, EEM, GLD, SLV, GDX and USO. It downloads Yahoo Finance daily data and TWS native five-minute regular-session `TRADES` history. Each run records a floor of January 1, 2007 and a common completed NYSE cutoff. Verified blocks use the [local replay input format](five-minute-inputs.md).

See the [dated coverage record for the November 27, 2026 cutoff](etf-coverage-2026-11-27.md).

## Run and resume

From the repository root, use a new directory directly under `data/etf_history/`. The example uses the local TWS port `7497` and share volume. Keep TWS connected while intraday requests are in flight.

```powershell
.\.venv\Scripts\python.exe -m equity_backtest.acquire_history --run-dir data/etf_history/etf_2007_run --port 7497 --volume-unit shares
.\.venv\Scripts\python.exe -m equity_backtest.acquire_history --run-dir data/etf_history/etf_2007_run --port 7497 --volume-unit shares --resume
```

Use `--stage daily`, `--stage intraday` or `--stage audit` to run one part. Auditing is offline and uses the archive already in the run directory. The first run freezes the cutoff and provider settings. Resume reuses completed requests and keeps attempted failures in the archive. Historical requests start at least two seconds apart across the TWS connections. `--workers` sets how many responses can be in flight, from 1 to 6. Twelve-month windows that fail to verify a session fall back to monthly and then per-session requests. IBKR historical availability and provider response times vary by period and contract. [IBKR historical bar documentation](https://www.interactivebrokers.com/docs/tws-api/doc/md/historical-bars) · [IBKR market data limits](https://www.interactivebrokers.com/docs/tws-api/doc/md/market-data-limitations)

An audit run before acquisition completes writes a dated progress directory. The final audit publishes verified bundles and seals the source archive for the run. If access rights change during an unpublished run, use `--retry-entitlements` with `--resume` to retry windows previously denied by TWS.

## Read the results

| File | Contents |
| --- | --- |
| `run.json` | Frozen scope, endpoint, configured volume unit and inception evidence |
| `provider.sqlite3` | Raw Yahoo and TWS responses, attempt metadata and payload hashes |
| `daily/<ticker>.provider.csv` | Yahoo provider rows with actions and adjusted close when available |
| `daily/<ticker>.json` | Canonical Yahoo snapshot with indicators and missing-session dates |
| `audit/symbols.csv` | Per-symbol totals, publication status and trailing shortfalls |
| `audit/sessions.csv` | One requested NYSE session per symbol, with both sources' status and the bar counts |
| `audit/missing_5m_bars.csv` | Every missing or unusable five-minute slot, expressed in UTC and New York time |
| `audit/anomalies.csv` | Invalid, duplicate, conflicting and out-of-window records, corporate actions and price-basis flags |
| `audit/verified_blocks.csv` | Every uninterrupted verified interval and the publication candidate |
| `manifest.json` | Final artifact hashes, contract identities and replay publication receipts |

`pre_inception` identifies dates earlier than documented launch evidence. The source may return its first usable row later than launch. The audit distinguishes that gap from launch exclusions, and separates empty responses from failures or unattempted periods. Unverified sessions can contain some or all expected timestamps when confirmation or price-basis checks failed. Use the session and anomaly ledgers to interpret those cases alongside the missing-bar ledger.

Each published symbol's `replay/<ticker>/config.toml` uses the latest verified block, with any more recent gap recorded in the audit. The replay folder appears after offline verification and contains a standalone intraday SQLite cache and its paired Yahoo daily snapshot. A block with no usable paired daily levels is listed in the audit with its release failure. Run the replay from the repository root.

```powershell
.\.venv\Scripts\python.exe -m equity_backtest.backtest_5m --config data/etf_history/etf_2007_run/replay/QQQ/config.toml
```
