# ETF daily and five-minute history acquisition

## Scope and approach

Collect Yahoo Finance daily and TWS five-minute history for QQQ, DIA, XLF, XLK, XLE, XLI, XLP, XLY, XLV, XLU, XLB, IYR, IWM, TLT, TIP, EMB, PFF, IBB, EEM, GLD, SLV, GDX and USO. Freeze January 1, 2007 as the request floor and the latest completed NYSE session at run start as the common cutoff. For newer instruments, classify sessions before the first documented trading date separately. An issuer launch date is evidence about inception and can differ from the first trading date. [PFF issuer page](https://www.ishares.com/us/products/239826/ishares-u-s-preferred-stock-etf) · [EMB issuer page](https://www.ishares.com/us/products/239572/ishares-msci-emerging-markets-etf)

- **Selected.** Build a resumable per-symbol archive and audit over the entire interval, including periods on both sides of a gap. Publish a separate replay cache for each symbol's most recent uninterrupted verified block. Enumerate all earlier verified blocks in the audit.
- **Alternative.** Finish the archive and audit before selecting any replay ranges. This preserves the same evidence and requires a second decision before running the backtester.

Run on the current branch. Use a new run-specific directory under `data/` and keep existing caches, configs and reports intact. Reuse the current NYSE grid, five-minute schema, daily indicator definitions and SPY-era safety checks where applicable. Acquisition uses Yahoo for daily data and TWS for intraday data.

## Daily acquisition

- Request daily OHLCV using `yfinance` with `auto_adjust=False` and `actions=True`. Preserve the returned data, including adjusted-close and corporate-action columns when supplied, and record request parameters, library version, timestamps and outcomes. Keep the raw payload alongside an independently generated replay-ready daily snapshot.
- Calculate the same daily Upper, Lower, Wilder ATR where applicable, EMA20 direction and volume indicators as the existing replay and daily snapshot workflow. Use completed sessions. Missing or defective daily data breaks indicator continuity, and warmup fields can be null. Retain provider rows and flags for audit, including duplicate, unexpected-date and invalid rows.

## Intraday acquisition

- Confirm the local TWS endpoint and historical-data access through a bounded initial probe. Verify one matching USD equity/ETF contract per symbol and record its conId, currency, primary exchange and routing. Keep ambiguous or unavailable contracts as unresolved audit outcomes. Treat a TWS head timestamp as a coverage hint. Record the configured volume unit.
- Use native five-minute `TRADES` bars in regular trading hours and preserve returned timestamps, OHLCV, weighted prices and provider error details. Download paced monthly windows, with shorter checked windows where the provider requires them. Retry incomplete or empty eligible sessions individually. Apply bounded retries, backoff and resumable checkpoints. Continue scanning across gaps and other symbols. Reuse completed requests when resuming the same frozen run. [IBKR historical bar documentation](https://www.interactivebrokers.com/docs/tws-api/doc/md/historical-bars) · [IBKR market data limitations](https://www.interactivebrokers.com/docs/tws-api/doc/md/market-data-limitations)
- Keep each provider response and its request outcome before canonicalization. Checkpoint a request only after its evidence is durable. On restart, reconcile any durable response whose checkpoint was interrupted. Verify a session only when a completed attempt establishes the full expected five-minute grid and there are no unresolved conflicting or invalid canonical bars. Preserve rejected, partial, off-grid and conflicting rows in the archive. Use the actual NYSE regular-session grid, including holidays, early closes and DST transitions.

## Audit and publication

- Produce a per-symbol summary, an exchange-session ledger, a missing-bar ledger with exact UTC and New York timestamps, an anomaly and conflict ledger and a verified-block inventory. Reconcile expected sessions and bars with complete, partial, empty, unresolved-request and pre-listing outcomes. Audit daily gaps independently of five-minute gaps. Separate provider-confirmed empty responses from access errors, timeouts and unattempted windows. Keep unverified starts visible when a listing date is unknown.
- Compare corroborating intraday and Yahoo open/close observations on matched dates using the existing common-factor price-basis check. Record suspected corporate actions and discrepancies with source values and flags. A corroborated, unresolved price-basis mismatch breaks a publishable intraday block. Count missing daily inputs separately and flag their effects on replay eligibility. Keep source prices and audit evidence intact.
- Find every contiguous verified block on the exchange-session grid. Publish the block with the latest end date for each eligible symbol, even if a trailing gap prevents that block from reaching the cutoff. Record the cutoff shortfall and every earlier block. Stage the intraday cache, daily snapshot and replay config together, verify the files offline, then publish that symbol's replay directory atomically. The cache records symbol, conId, volume unit and verified endpoints. Report a zero-publication result when there is no verified block. If a valid block cannot be released because its paired daily input or the replay smoke check fails, record the block and the release failure.
- Store provider and file hashes, the symbol statuses, reasons for all excluded sessions, and the replay file paths in a run manifest. Update the README and input documentation with acquisition, resume, audit and replay usage, and add a dated coverage report after the run.

## Verification

Test launch bounds, half-days, timeout recovery, no-data responses, conflicts, contrasting price bases, publication after an internal or tail gap, and archive resume with synthetic fixtures. Use live bounded probes to verify provider shape and units before the full run. After the full run, reconcile every symbol and date, independently verify each published cache, and report any symbol or period the providers leave unresolved.
