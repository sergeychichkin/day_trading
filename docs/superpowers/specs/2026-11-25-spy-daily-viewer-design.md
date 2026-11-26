# SPY daily viewer

## Goal and scope

Download SPY daily OHLCV using `yfinance` from January 1, 2000 through the latest completed US regular session. Keep a separate local cache so the viewer works without Yahoo access after download. Use Yahoo's non-dividend-adjusted OHLC so the chart shows the historical session price levels. Leave QQQ's minute data, viewer, and backtests unchanged.

## Approaches

- **Chosen: a separate `/spy` page on the existing local server.** Reuse the QQQ styling, bundled chart library, and native Month picker while keeping the daily data and endpoints separate.
- **Alternative: standalone HTML.** It would open without the server, but each refresh would require rebuilding the report instead of just refreshing the data cache.

## Chart and calculations

- Show one candlestick per completed trading session. The Month picker matches QQQ's control, starts at January 2000, defaults to the latest available month, and fits the selected month on screen. Preserve the visible dates when resized. A single cached session appears on its own unless the user explicitly requests adjacent sessions.
- Put volume bars and their 20-session simple moving average together in a lower scale band. Keep price autoscaled and disable manual vertical price scaling so candles cannot move into the volume band. The SMA includes the selected day's volume and starts on the 20th session.
- Overlay a 20-session EMA of close on the price pane. Seed it with the first 20 closes' average. Color it green when it rises, red when it falls, and retain the prior color on ties; use neutral until a direction is known.
- For each of the 10 preceding trading sessions, calculate `(high - open) / open` and `(open - low) / open`. Average each series separately to get `avg_max` and `avg_min`. Overlay `today_open × (1 + avg_max)` and `today_open × (1 - avg_min)`. Both levels are fixed at today's open, exclude all of today's data except the open, and first appear on the 11th session.
- Calculate indicators over continuous history so they do not restart at month boundaries or exchange holidays. A missing scheduled session resets each window; the chart leaves lines blank until enough consecutive input is available. Show daily OHLCV and indicator values in the crosshair readout.
- Publish a validated snapshot atomically; reject older cutoffs and refreshes that omit previously cached sessions. Report missing sessions, including those after the latest cached bar, without moving the default month away from the latest available bars.

## Verification

Check that prior-session calculations exclude today's high and low and continue across month boundaries. Verify that the picker opens the requested month, both volume series share the lower band, and EMA colors follow direction. Record the initial download and live-cache smoke results in the [README](../../../README.md#spy-daily-viewer).
