# SPY five-minute viewer

## Intent

The original January 2000 bulk start below did not pass the live TWS probe.
The delivered workflow instead publishes the earliest uninterrupted range
through the requested completed NYSE session, and refreshes it without
crossing a gap. See the [dated coverage record](../../spy-5m-coverage.md)
for the measured start and operating commands.

Make it possible to compare intraday SPY price and regular-session VWAP with the two levels already shown on the daily chart. Start the history at January 1, 2000 (first expected NYSE session: January 3). The coverage target is the latest NYSE regular session that has closed when the run starts, even if TWS has not returned that session yet. Do not change the existing SPY daily or QQQ viewers, place orders, or imply that incomplete history has been verified.

## Approaches

- **Chosen: a separate `/spy/5m` page** on the existing local server. It reuses the QQQ-style Month picker and chart styling without asking the daily chart to handle intraday navigation.
- **Alternative: a Daily / 5-minute toggle** on the SPY daily page. It keeps the views one click apart but couples their navigation and data loading. A separate page was preferred.

## Download and storage

- Use a read-only historical-data connection to the running TWS API. Qualify a unique USD SPY stock/ETF contract on SMART and verify its primary listing and contract ID. Reuse the existing connection and request patterns where possible without changing QQQ's symbol, bar size, or cache behavior.
- Request native 5-minute regular-hours `TRADES` bars, including OHLC, volume, and the broker's per-bar weighted average price (WAP). Keep timestamps in UTC and map them to America/New_York session dates using the NYSE calendar, including early closes. Expect 78 bars in a normal 390-minute session; use the exchange schedule for shorter days.
- Probe the first January 2000 trading week and one recent completed session before the full backfill. Verify contract identity, timestamps, bar counts, usable volume and WAP, and spot-check that price units are comparable to the Yahoo daily series. A usable January sample with isolated gaps allows the backfill to continue while keeping gaps visible and retryable. If the old or recent probe produces no usable sessions, stop and report the exact response rather than changing the start date or source. If the overlapping samples show a systematic price-basis mismatch, stop and report it before enabling the overlays; never rescale the daily values automatically.
- Use paced, resumable requests in bounded batches that the probe shows TWS accepts. Save data and coverage as batches complete to a separate SQLite cache, default `data/spy_5m.sqlite3`. Preserve the first accepted OHLCV and valid WAP; a matching retry may fill previously unavailable WAP, but differing valid values are flagged as conflicts. Distinguish absent bars and unavailable WAP from requests that were not completed or verified. Resume missing/unverified work without re-downloading complete sessions. Report the actual first and last bars, coverage, and remaining gaps; do not claim a full backfill until validated against the exchange calendar.
- Keep the configured broker volume unit consistent across refreshes. Stop and report connection, permission, or unresolved pacing problems instead of repeatedly hammering TWS. The viewer reads cached data and works with TWS closed.

## Chart and calculations

- The separate page uses the same native Month picker, restricted to the published range and defaulting to the latest month with cached bars. Load the selected month, open at its first cached session with that session in view, and allow horizontal panning within the month. Mark session boundaries and count coverage only inside the published range; identify any outstanding requested cutoff separately.
- Show SPY five-minute candlesticks and session VWAP. Compute VWAP as the running sum of `bar WAP × bar volume` divided by running volume, starting from the regular-session open. A zero-volume bar contributes no weight; there is no VWAP before positive cumulative volume. Do not substitute OHLC averages for broker WAP. If an expected bar is missing or a positive-volume bar has unusable WAP, omit VWAP from that point to the end of the session. Restart cleanly at the next session open.
- By New York session date, read `upper` and `lower` directly from the existing SPY daily snapshot. Plot each as a horizontal line from the session open to its close, without drawing across overnight gaps. These are exact values, not percentages reapplied to the first intraday candle. As a SPY sanity check, accept upper levels from 100% to 200% of its daily open and lower levels from 50% to 100%. Reject bad values individually. Hide levels when the first or last bar is missing or their price basis cannot be verified. Explain the selected session's level status without removing its candles or VWAP.
- The crosshair shows New York date and time, OHLCV, VWAP, and the daily levels. Render missing bars as gaps. The page explains how to populate an absent intraday cache and still shows intraday bars if the daily snapshot is absent.

## Verification

Test normal and early-close calendars, gaps, zero volume, VWAP resets, exact joins to the daily snapshot, and resumption without duplicates or a changed QQQ cache. In the browser, check month navigation, gap visibility, and that VWAP and daily lines terminate at session boundaries. Verify the old and recent probes through TWS, run the backfill, and record the observed coverage and any provider limitations.
