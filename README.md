# QQQ opening-session viewer

Browse Interactive Brokers one-minute QQQ candles for the US regular session from January 2020 onward. The month picker opens the first available trading day's 09:30–11:30 New York time window. Drag the chart to see later bars and adjacent sessions. Each session open has a vertical divider, and the crosshair legend shows the selected minute's OHLC and volume. The blue line is the 20-bar volume SMA, which uses the previous session's bars at the open. The purple VWAP estimate starts fresh each regular session and weights each minute's average of high, low, and close by its volume.

## Setup on Windows

Use Python 3.13 with TWS open and connected to an account with access to QQQ historical data. In Global Configuration → API → Settings, confirm socket access, the local port, and the US-stock volume unit. The local installation used port 7497 and volume in shares. The acquisition client is the pinned ib_async TWS client and only sends contract and historical data requests. [IBKR historical bar documentation](https://www.interactivebrokers.com/docs/tws-api/doc/md/historical-bars)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

## Acquire or refresh history

The one-day probe verifies the January 2, 2020 regular session. The full run resumes from the existing cache and targets the most recent completed regular session at startup. Use the same confirmed volume unit when refreshing. Before fetching missing data, the downloader compares the January probe against the cached reference to catch an upstream unit change. The default SQLite file is data/qqq.sqlite3.

```powershell
.\.venv\Scripts\python.exe -m qqq_viewer.backfill --port 7497 --volume-unit shares --probe
.\.venv\Scripts\python.exe -m qqq_viewer.backfill --port 7497 --volume-unit shares
```

Full runs request at most one calendar month per block and retry incomplete sessions individually. The terminal reports the observed New York range, missing minutes, unverified sessions, and conflicting minutes. The cache preserves provider OHLCV and a request ledger. When responses for the same minute disagree, the first accepted bar remains on the chart and the chart marks the affected candle.

## Browse the cache

```powershell
.\.venv\Scripts\python.exe -m qqq_viewer.server --port 8765
```

Open [the local QQQ viewer](http://127.0.0.1:8765/). The viewer can run as the backfill progresses and works after TWS is closed. Reload the page to refresh month availability. The coverage line shows received and expected regular-session minutes for the selected month and flags gaps and conflicts. Scheduled closures and early closes use the exchange calendar. Overnight and weekend gaps are compressed on the chart.

## SPY daily viewer

Install the project dependencies, download the data, then start the same local server. SPY does not require TWS.

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m qqq_viewer.spy_daily
.\.venv\Scripts\python.exe -m qqq_viewer.server --port 8765
```

Open [the SPY daily viewer](http://127.0.0.1:8765/spy). The Month picker jumps to a month; drag the chart to pan into adjacent months. The price chart shows the close EMA(20) and two levels fixed at each day's open, based on the previous 10 sessions' average percentage moves from open to high and open to low. Volume and its SMA(20) share the lower area.

The downloader uses Yahoo Finance daily OHLCV without dividend adjustment, from January 2000 through the last completed US regular session. It prints missing NYSE session dates and writes `data/spy_daily.json`. Rerun the downloader to refresh the snapshot, and reload the page. The viewer reads locally and does not need internet access to display a downloaded snapshot.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
# Install Chromium only if the Windows installation has no Microsoft Edge.
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest -q
```

Charts use the locally bundled [TradingView Lightweight Charts](https://www.tradingview.com/lightweight-charts/) 4.1.1.

## Verified initial backfill

The local cache covers January 2, 2020 at 09:30 through November 20, 2026 at 15:59 New York time. It contains 673,320 one-minute bars across 1,732 regular sessions. The calendar audit found zero missing minutes and zero conflicting minutes. Repeating the backfill left the cache counts unchanged.

## Opening-range backtest

The command below compares Legacy and Revised rules for the enabled entries
using the local one-minute QQQ cache. The replay permits sequential trades,
with one open position at a time. Dates are inclusive and default to
January 1 through December 31, 2020.

To generate the report for all of 2020, run this from the repository root:

```powershell
.\.venv\Scripts\python.exe -m qqq_viewer.backtest_opening_range --config config/QQQ.toml --start 2020-01-01 --end 2020-12-31
```

For a shorter date range:

```powershell
.\.venv\Scripts\python.exe -m qqq_viewer.backtest_opening_range --config config/QQQ.toml --start 2020-02-03 --end 2020-04-30
```

Use a TOML file per ticker and point `cache` to its regular-session SQLite
cache. Set `enabled = false` in any entry table to exclude that entry.
`[entry3]` is optional and disabled when omitted. Each entry has its own ATR
multipliers and risk percentage. `[strategy]` holds the shared exits, buying
power, tick size, and slippage.

Open `output/QQQ_opening_range_rule_comparison_2020-01-01_2020-12-31.html`
in a browser. With all three entries enabled, each rule version includes
Entry 1 only, Entry 2 only, Entry 3 only, Entries 1 and 2, and All enabled
entries, each with base and doubled slippage. The report opens on Revised.
Switching **Rules** keeps the selected case in **View**. Use **Show** in the
Executed Trades Log to filter by entry, or **RID [LazyBear]** to toggle the
same range bands the backtest uses. Select an order to inspect its working
window, or a trade to see its fills and active stop path.

Entry 3 looks for a bullish candle from 09:34 through 11:59 New York time
that closes strictly above the prior RID upper band with its high strictly
below VWAP. The prior band must contain at least `min_rid_bars` completed
candles (default 3, configurable from 1 to 25 in `[entry3]`). The breakout
candle does not count. The first later bearish candle is the signal, even if
price has returned inside the prior range. The buy stop is the highest high over the
signal candle and previous four candles, plus `entry_atr × ATR(20)`. The
initial stop is the lowest low over the signal candle and the previous
`stop_lookback` candles, minus `stop_atr × ATR(20)`. Both multipliers
default to 0.25 and the stop lookback defaults to four. Orders become active
the next minute; a signal stamped 11:59 cannot place a working order before
noon. Entry 3 uses the existing position sizing and exits.

Revised Entry 2 accepts a bear candle or doji after four consecutive bullish candles. The signal or previous candle must have a high strictly above its own VWAP. Under Revised rules, for all entries, a bear candle entirely below VWAP starts a wait for the next bull candle. At that bull candle's close, the replay uses the preceding lows and configured ATR buffer to calculate a candidate stop, which applies starting on the next minute if it raises the active stop. A later bear candle can start another sequence.

The replay verifies the ticker and session bounds for canonical bars and completion records. It requires complete, conflict-free sessions and uses earlier cached bars for indicator warmup only. The report records its input fingerprint and cost assumptions. Base and doubled-slippage scenarios include modeled IBKR Pro Fixed broker commissions. The additional regulatory and venue fees depend on the applicable schedule and routing and are excluded here.
