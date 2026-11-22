# EGM Lite checklist — Entry 2 Goldfish fixes

Starting commit 88fad71. The working tree was clean.

- [x] [Runner](qqq_viewer/backtest_opening_range.py) protects SQLite WAL, shared-memory and journal paths and aliases as report destinations.
- [x] [Runner](qqq_viewer/backtest_opening_range.py) verifies canonical bar sources and session completion by ticker and bounds, including warmup.
- [x] [Report chart](qqq_viewer/static/opening-range.js) shows filled orders' effective working windows alongside signal and fill details.
- [x] [Replay](qqq_viewer/opening_range.py) sizes whole shares with exact arithmetic at trigger and modeled fill boundaries.
- [x] [Replay](qqq_viewer/opening_range.py) rejects nonpositive modeled exit fills before computing commissions.
- [x] [Report chart](qqq_viewer/static/opening-range.js) shows New York date and minute on equity and drawdown crosshairs.
- [x] [Report chart](qqq_viewer/static/opening-range.js) uses ticker tick precision for order levels and the candle price scale.
- [x] [Replay tests](tests/test_opening_range.py) cover the three Entry 2 noon boundaries.
- [x] Add regressions to [runner tests](tests/test_opening_range_runner.py) and [browser tests](tests/browser/test_opening_range_report.py). Update [README](README.md) for the displayed order window and archive behavior.
- [x] Run the full pytest suite, regenerate the 2020 comparison, reconcile all six cases and verify the original 2020 report checksum.

Deferred empirical tests and pre-review exclusions are limited to live broker execution, which is outside the historical replay.

## Verification

- All 52 tests passed with pytest cache disabled. Node syntax check and git diff --check passed.
- Rebuilt the 2020 QQQ report from 98,310 verified bars. The embedded data matched the prior report across all six cases.
- Verified the protected input cache and original HTML archive by SHA-256.
- Checked the new working window, stop guide timestamps, and equity and drawdown minute labels in Edge.

### Assumption Lifecycle Legend

- `[⚠️ UNVERIFIED]` indicates an external contract awaiting evidence.
- `[⏳ ACK]` indicates an accepted assumption with an empirical test scheduled.
