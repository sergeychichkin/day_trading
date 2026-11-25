# Entry 3: RID breakout below VWAP

## Intent

Backtest a long entry after a bullish breakout above the chart's active RID
range, while the breakout candle remains entirely below VWAP. Entry 3 is a
backtest strategy only; it does not send orders to a broker.

## Approaches

- **Chosen: one RID calculation in Python.** Calculate RID once per session.
  Use its prior-band boundaries in replay and serialize its segments for the
  chart. Keep the chart's existing daily reset, one-cent boundary tolerance,
  and 25-bar segment limit.
- **Alternative: mirror the JavaScript calculation in Python.** This would
  require a smaller chart change but could allow the displayed bands and
  backtested boundaries to disagree.

## Setup and order

- Evaluate completed one-minute candles from **09:34 through 11:59**, New York
  time, inclusive. The breakout candle must satisfy `close > open`,
  `close > prior RID upper band`, and `high < candle VWAP`. A wick or a new RID
  segment alone does not qualify. Use unrounded session VWAP for the decision.
- The prior RID band must contain at least `min_rid_bars` completed candles
  (default `3`, inclusive, configurable from `1` to `25` in `[entry3]`).
  Do not count the breakout candle or change which bands the chart displays.
- Once armed, wait for the first later candle with `close < open`. A doji
  does not qualify. Re-entering the old range or seeing another breakout does
  not cancel or replace the armed setup. Consume it on the first bearish
  candle, then require a fresh breakout for another setup. Never carry it
  across sessions.
- Buy stop: highest high of the signal candle and its four preceding candles,
  plus `entry_atr × ATR(20)`. Initial stop: lowest low of the signal candle and
  its preceding `stop_lookback` candles, minus `stop_atr × ATR(20)`.
  Default `entry_atr` and `stop_atr` to `0.25`, and `stop_lookback` to `4`.
- Use the existing tick rounding, risk and buying-power sizing, one-position
  rule, working-order replacement, commissions, slippage, and exits. A new
  order becomes active on the next minute; a signal stamped 11:59 cannot
  place a working order before noon. Keep the existing Entry 1/2 priority
  on simultaneous signals. If a position is open at signal evaluation, do
  not queue that signal for later.

## Configuration and reporting

- Add an optional `[entry3]` TOML table, disabled when absent. Use the same
  Entry 3 setup for Legacy and Revised, each with its existing exit rules.
- When enabled, include an **Entry 3 only** scenario and an **All enabled
  entries** scenario when Entry 3 and another entry are enabled. Retain
  existing Entry 1/2 comparisons and base/doubled-slippage cases. Do not add
  every pairwise combination.
- Label Entry 3 in the chart and trade rows. Offer **Entry 3** in the Executed
  Trades Log filter when the report includes Entry 3 scenarios; keep the
  previous sorting behavior.

## Verification

Cover strict breakout/VWAP comparisons, RID reset and expiry, re-entry before
the first bear, no same-candle signal, noon cutoff, configured order levels,
and unchanged Entry 1/2 behavior when Entry 3 is disabled. Verify the chart
renders the replay's RID segments and the report exposes the new scenario,
labels, and trade filter.
