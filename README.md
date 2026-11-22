# QQQ opening-session viewer

Browse Interactive Brokers one-minute QQQ candles for the US regular session from January 2020 onward. The month picker opens the first available trading day's 09:30–11:30 New York time window. Drag the chart to see later bars and adjacent sessions. Each session open has a vertical divider, and the crosshair legend shows the selected minute's OHLC and volume. The blue line is the 20-bar volume SMA, which uses the previous session's bars at the open.

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
