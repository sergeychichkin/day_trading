# SPY five-minute Entry 1 backtest

## Purpose

Compare SPY entry time windows and the effect of exiting at the entry day's close versus the next session's open. Replay historical bars only; do not place orders or change the SPY chart, acquisition caches, QQQ strategy, or existing reports.

## Approaches

- **Chosen: a separate five-minute runner with a per-ticker TOML.** Reuse QQQ's Wilder ATR and IBKR Pro Fixed commission functions, the chart dependency, and the report's layout patterns. Keep SPY trade rules and the report payload independent.
- **Alternative: generalize the QQQ strategy runner now.** It would share more orchestration but would tie two different setups together and add regression risk. A common ticker settings layout and a read-only data boundary are enough for now.

## Input and settings

- Add `config/SPY.toml` for the ticker, intraday and daily cache paths, entry window, ATR buffers, order lifetime, equity/risk, tick size, buying-power cap, simulated execution costs, and exit switches. Use the per-ticker layout for future instruments, but reject any ticker that does not match a supported cache identity. The first adapter is SPY.
- Use the verified, conflict-free five-minute SPY cache and the existing Yahoo daily snapshot, joined on New York regular-session date. Use the daily chart's exact `upper` value (`avg_max` applied to the daily open), not a percentage reapplied to the intraday open. Treat a missing or malformed level or inconsistent opening price basis as an unavailable setup. For a trading decision compare only the two sources' opening prices, not the current day's close or range. Reject a relative difference greater than 5% (`abs(intraday_open / daily_open - 1)`); a read-only check of all 3,249 currently overlapping sessions found a maximum difference of 1.3906%. Report excluded days and reasons.
- Default to the published intraday date range; expose inclusive `--start` and `--end` for signal sessions. Use earlier verified bars only for warmup. Reject gaps or conflicts inside the requested replay input; do not invent candles. Record settings, effective range, any skipped next-open boundary, and fingerprints of both inputs so runs can be compared.

## Entry 1 state machine

1. An eligible long signal is the **first** candle with `close > open` and `close > daily upper`, whose New York candle **start** falls within the inclusive entry window. Defaults are `entry1_start_time = "09:30"` and `entry1_end_time = "09:40"` (09:30, 09:35, 09:40 bars). Equal prices and dojis do not qualify.
2. Beginning after that candle, wait for the first `close < open` candle on the same regular trading day. It may be after the signal window. If none appears, no order is placed. Only one setup can latch per day; an expired order, rejected order or stopped trade does not re-arm it.
3. At the bear candle's close, calculate the buy-stop trigger as its high plus `entry_atr_buffer × ATR(20)`. Set the fixed initial protective stop as the lowest low from the current session open through that bear candle, less `stop_atr_buffer × ATR(20)`. Both buffers default to `0.25`; round buy trigger up and stop down to the configured tick. Reject a missing ATR or nonpositive stop/risk distance. Do not recalculate the order or protective stop after submission.
4. Use Wilder ATR seeded from the first 20 completed five-minute regular-hours bars in the published cache. Carry it over verified sessions and include the overnight gap in the first bar's true range. Use the value as of the bear candle close, including that bar; no future candle enters the calculation. Changing `--start` must not change ATR on overlapping candles drawn from the same cache.
5. Activate the stop order at the next bar opening (never on the bear bar). Its valid interval is activation inclusive, expiration exclusive, for `entry_order_valid_minutes = 120` minutes by default, capped at that session's exchange close. Accept lifetimes in positive multiples of five minutes. Never carry an entry order overnight.

## Execution, risk, and exits

- Each enabled exit scenario starts independently with `$100,000` and compounds realized P&L. At submission, risk `1%` of current equity based on the rounded trigger-to-stop distance. Use whole shares, subject to a configurable cash cap greater than zero and no more than `1×` equity (default `1×`). Do not model leverage or financing. Budget entry slippage and commission for cash affordability; do not resize later using the fill candle's range. If an opening gap makes the fixed-size order unaffordable, reject it and log the reason.
- Entry fill reference is `max(bar.open, buy_stop)` when the market reaches the buy stop. Use `min(bar.open, protective_stop)` for a subsequent protective stop. If the buy stop and protective stop could both trigger intrabar while their order is unknown, use the adverse path (entry then stop) and flag the trade as ambiguous. An entry filled at the candle open followed by a touched stop is not ambiguous. Charge slippage adversely on both legs using configurable `max($0.01, 1 basis point of reference price)` defaults, and itemize slippage, opening gap amounts, and IBKR Pro Fixed per-order commission. These are OHLC-based simulated fills, not reconstructed quotes or guaranteed losses.
- **Exit 1 (default enabled):** If not stopped, exit at the entry session's final close, including scheduled early closes. Process protective stops before the same-day time exit.
- **Exit 2 (default disabled):** If not stopped during the entry session, hold across the closed period and exit at the next NYSE regular-session open. Model no extended-hours executions; overnight movement is reflected in the opening fill. Close the old position before evaluating new signals for that session. If the next verified open is just outside the signal range, use it only to close the open trade and disclose the exit date. If no verified next open exists in the cache, exclude that final session from next-open entries and disclose why.
- If both exits are enabled, run both independently into one report; if neither is enabled, reject the settings. Exposure beyond plan risk can occur with gaps, slippage and fees.

## QQQ-style standalone report

Write a separate self-contained HTML report with an executive scenario summary, closed-balance equity and drawdown, a buy-and-hold price benchmark over the selected signal range, an order/event log, and an executed-trades log. Include a month-selectable five-minute price chart with daily levels and entry, stop, and exit markers. Selecting a log row should jump to the applicable session; label next-open exits and ambiguous bars. Show trade counts, dates, net P&L, costs, and rules so results can be checked. Keep new report assets separate where QQQ-specific controls do not apply.

## Verification

Use synthetic tests for timing, exact window boundaries, strict candle tests, first-bear behavior, ATR warmup, day-low look-ahead, expiration, tick rounding, whole-share sizing, gap fills, intrabar ambiguity, both exits under early-close/weekend calendars, and the unavailable next-open boundary. Verify independent account balances when both exits are enabled. Run both modes on verified SPY data and check the report in a browser without touching the caches or QQQ outputs.
