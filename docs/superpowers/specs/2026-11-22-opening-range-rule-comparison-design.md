# QQQ opening-range rule comparison

## Purpose and approach

Compare the current and revised rules in one opening-range backtest report using identical market data and execution assumptions. The user chose a rule-version selector within a single report. A separate revised report would keep each report simpler but require switching files to compare equivalent cases. The combined report reuses the existing replay and report, with an explicit rule version for each replay and independent account balances for all cases.

## Strategy rules

- **Legacy** reproduces the current replay, including the bearish-only Entry 2 signal and the immediate VWAP-based trailing-stop calculation.
- **Revised Entry 2** accepts a bear candle or an exact doji (`close <= open`) following at least four consecutive bullish one-minute candles in the same regular session. A signal qualifies only if its high is strictly above its own session VWAP or the previous candle's high is strictly above that candle's session VWAP. An unavailable VWAP cannot satisfy its comparison. Entry 1 keeps its current signal rules.
- **Revised exit for both entries.** A completed bear candle with its high strictly below its session VWAP arms the trailing update for that open trade. The first subsequent completed bull candle consumes the pending update. Calculate the candidate as the lowest low of the four immediately preceding candles, excluding the bull candle, minus the existing configured buffer (0.25 times the one-minute ATR at the bull candle's close in the default QQQ settings). Use the configured trailing lookback and round down to the tick as today. A strictly higher candidate becomes active on the next minute, subject to the scheduled exit. Once the bull candle consumes the update, a later qualifying bear candle can arm it again, even if the candidate did not raise the stop. Each new position starts with no pending update. Existing protective-stop processing retains priority over candle-close signals.

## Report

- Add a `Rules` selector with `Legacy` and `Revised`, defaulting to `Revised`. Keep the existing scenario selector for entry combination and slippage. Switching rule version keeps the selected scenario and updates the summary, performance charts, orders, and trades together. Each rule version uses the same configured entry combinations, dates, starting balance, and cost assumptions. With both entries enabled, this yields six cases per version.
- Add a sortable `Entry` column beside `Date` for both rule versions. Display `1` or `2` from the filled order's recorded entry type.
- Add the `RID [LazyBear]` price overlay to both versions. Use the QQQ reference report's gray horizontal upper and lower bands, enabled by default with a visibility toggle. Initialize the bounds to the first one-minute candle's high and low each regular session. Keep a range for at most 25 bars, and reset it to the current candle's high and low when its close is outside the strict $0.01 interior on either side or the bar cap is reached. Stop drawing at session boundaries. RID affects the chart display only.
- Generate the combined report under a distinct default filename so the existing reports remain available.

## Verification

Compare each Legacy case with its current counterpart on the same available input, including trades, order events, and metrics. Check the revised Entry 2 candle types and both VWAP comparisons, and exercise waiting, next-minute activation, repeated sequences, and per-trade reset for the revised stop. Verify the selectors, sortable Entry values, and RID display in the generated HTML.
