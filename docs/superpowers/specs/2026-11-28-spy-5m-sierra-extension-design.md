# SPY five-minute Sierra + TWS history extension

## Intent and why

Extend the local uninterrupted 5-minute `SPY` cache (`data/spy_5m.sqlite3`) backward from `2013-12-24` to **`2008-05-05`** (`4,669` sessions through `2026-11-24`, up from `3,249`), using the downloaded Sierra Chart consolidated US equities feed (`C:\SierraChart\Data\SPY.scid`) plus TWS on port `7497` to patch the 12 sessions where Sierra has missing bars.

## Goals, non-goals, and anti-goals

- **Goals:** Produce a verified, conflict-free, strictly uninterrupted `2008-05-05`–`2026-11-24` `FiveMinuteStore` cache compatible with `qqq_viewer.spy_5m_contiguous --verify`, `qqq_viewer.backtest_spy_5m`, and the `/spy/5m` browser viewer, while keeping future TWS tail updates working.
- **Non-goals:** Changing `spy_5m_replay.py` entry/exit rules, modifying QQQ (`data/qqq.sqlite3`), or extending past `2026-11-24` (the current `spy_daily.json` cutoff).
- **Anti-goals:** Overwriting the existing `2013-12-24`–`2026-11-24` TWS bars/exact `wap` values, inventing synthetic prices for missing bars, or losing provenance of which sessions came from Sierra vs. TWS.

## Constraints

- `SPY.scid` (`2008-05-05`–`2013-12-23`, `971,895` records) consists of 1-minute `float32` OHLCV bars (`trades=0`, exact penny prices within `7e-6`, share volume) with no stored bar VWAP.
- `FiveMinuteStore`, `spy_5m_contiguous`, and `backtest_spy_5m` require non-null `wap` on every nonzero-volume bar and strict continuity metadata (`contract='756733'`, `volume_unit='shares'`).
- A live check confirmed `SPY.scid` has `1,408` complete sessions in `2008-05-05`–`2013-12-23` and `12` incomplete sessions (`2009-09-04`, `2009-10-09`, `2010-01-08`, `2010-05-06`, `2010-07-26`, `2010-11-16`, `2010-12-31`, `2013-03-21`, `2013-05-24`, `2013-06-11`, `2013-06-20`, `2013-11-11`); a live TWS probe confirmed all `12` return `78/78` usable bars.

## Approaches

1. **Hybrid Sierra + TWS patch (Chosen):** Build a Sierra `.scid` reader/aggregator (`qqq_viewer/spy_5m_sierra.py`) and assemble a combined cache (`data/spy_5m_extended.sqlite3` built & verified first, then promoted to `data/spy_5m.sqlite3` with a backup of the pure-TWS file) containing the `1,408` complete Sierra sessions, the `12` TWS-patched sessions, and the `3,249` existing TWS sessions.
   - *Tradeoff:* Uses TWS briefly for 12 dates, in exchange for 18.5 years of strictly uninterrupted history.
2. **Sierra-only strictly uninterrupted:** Rejects anything past the first Sierra gap (`2013-11-11`), gaining only 30 trading days (`2013-11-12`–`2013-12-23`).
3. **Sierra-only with gap-skipping:** Skips the 12 incomplete dates in the backtest runner and resets ATR warmup, breaking the strict continuity invariant.

## Decisions made

- **Target file:** Promote the verified combined database to `data/spy_5m.sqlite3` (keeping a backup copy `data/spy_5m_tws_2013_2026.sqlite3`), so `config/SPY.toml`, `/spy/5m`, and future `spy_5m_contiguous` tail refreshes work without config changes.
- **Aggregation & `wap`:** Bucket 1-minute `.scid` records onto the NYSE 5-minute grid (`open = first.open`, `high = max`, `low = min`, `close = last.close`, `volume = sum`, prices rounded to 2 decimal places). Compute `wap` for 1-minute Sierra bars as volume-weighted typical price $\sum \frac{H+L+C}{3} \cdot V / \sum V$ (falling back to unweighted mean typical price if zero volume, matching `server.py` QQQ VWAP convention; `spy_5m_replay.py` does not use `wap`).
- **Provenance & TWS preservation:** Copy the existing `2013-12-24`–`2026-11-24` TWS rows unchanged (`bars`, `sessions`, `attempts`, `metadata`), record `attempts.diagnostic = 'sierra-scid-1m'` for the `1,408` Sierra sessions and `'tws-1d-patch'` for the `12` TWS gap sessions, and verify the entire `2008-05-05`–`2026-11-24` cache with `spy_5m_contiguous.verify_published`.
- **Left to implementation:** Binary-search bounding of `.scid` reads, handling any non-`trades=0` record defensively, synthetic unit tests for `.scid` parsing/aggregation/stitching, and updating `docs/spy-5m-coverage.md`.
