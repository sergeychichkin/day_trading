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

Open [the SPY daily viewer](http://127.0.0.1:8765/spy). The Month picker jumps to a month and fits its price range. Drag horizontally to pan into adjacent months. A month with only one cached session initially shows just that session; use **Show adjacent sessions** to restore the surrounding chart. The chart shows the close EMA(20) and two levels fixed at each day's open, based on the previous 10 sessions' average percentage moves from open to high and open to low. Volume and its SMA(20) share the lower area. Indicators carry across months and scheduled market closures, but restart their warmup after a missing scheduled session.

The downloader uses Yahoo Finance daily OHLCV without dividend adjustment, from January 2000 through the last completed US regular session. It prints missing NYSE session dates and writes `data/spy_daily.json`. Rerun the downloader to refresh the snapshot, and reload the page. A refresh that is older than the installed snapshot or omits previously cached days is rejected. The footer distinguishes the last cached bar from the date the exchange calendar was checked through, and warns about missing sessions after the last bar.

For a custom snapshot, pass its path to both commands: `qqq_viewer.spy_daily --cache <path>` and `qqq_viewer.server --spy-cache <path>` (each run with `.\.venv\Scripts\python.exe -m`). The viewer reads locally and needs no internet access to display a downloaded snapshot.

**Initial download verification (November 25, 2026):** Yahoo returned 6,765 bars, from January 3, 2000 through November 24, 2026, with no missing NYSE sessions. The live-cache browser smoke check opened both January 2000 and November 2026 and verified the displayed date ranges.

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

## SPY five-minute viewer

Open TWS with socket clients enabled. Use the port and volume unit configured in TWS; the example below uses port `7497` and volume in shares. The download sends historical data and contract requests only.

```powershell
.\.venv\Scripts\python.exe -m qqq_viewer.spy_5m_contiguous --port 7497 --volume-unit shares
.\.venv\Scripts\python.exe -m qqq_viewer.server --port 8765
```

The same download command builds a fresh cache or refreshes an existing published range. A fresh build checks months backward from the last completed NYSE session to find the uninterrupted range; a refresh checks forward from the installed endpoint and cannot advance past an incomplete session. It resumes staged work in `data/spy_5m_contiguous_audit.sqlite3` and publishes verified bars to `data/spy_5m.sqlite3`. Use `--through YYYY-MM-DD` to fix the cutoff; use `--floor YYYY-MM-DD` on a fresh build to change the earliest date considered (default `2004-01-23`). A gap that blocks the requested cutoff is reported and leaves later bars in staging. Use a separate `--staging-db` for separate viewer caches. Match the downloader's `--db` to the server's `--spy-5m-db`; the server's `--spy-cache` selects the Yahoo daily snapshot.

Open [the separate five-minute viewer](http://127.0.0.1:8765/spy/5m). The picker covers published months and opens the first cached session in the selected month. Drag horizontally to browse the month. The purple line is session VWAP, using IBKR's five-minute weighted prices and volume. A missing bar or unusable positive-volume weighted price stops VWAP until the next open. The amber and blue lines copy the SPY daily upper and lower levels by New York date. The legend identifies a missing, invalid, or withheld level for the selected session. The footer shows the published range separately from an outstanding requested cutoff. The viewer works with TWS closed.

**Published coverage verified November 25, 2026:** December 24, 2013 through November 24, 2026; **3,249 sessions and 252,450 bars**, with no missing, unverified, or conflicting bars. January 23, 2004 was the earliest observed full session, but the older data had gaps. See the [dated probe and coverage record](docs/spy-5m-coverage.md) for evidence and the offline verification command.

## SPY five-minute Entry 1 backtest

Run the replay from the repository root with the verified intraday cache and SPY daily levels already installed. It reads both locally; TWS does not need to be open. The default backtest uses the full published range and creates a separate standalone HTML report under `output/`.

```powershell
.\.venv\Scripts\python.exe -m qqq_viewer.backtest_spy_5m --config config/SPY.toml
.\.venv\Scripts\python.exe -m qqq_viewer.backtest_spy_5m --config config/SPY.toml --start 2020-01-01 --end 2020-12-31 --output output/SPY_5m_2020_exit3.html
```

[`config/SPY.toml`](config/SPY.toml) controls the entry window, the two ATR buffers, the order lifetime, equity and risk, and execution costs. Signal times label five-minute candle **starts**, in New York time. Only the first qualifying bull close can arm a setup; the first later bear can occur after the signal window. At the bear close the runner fixes the buy trigger and protective stop from the information available then, including the low so far, before allowing a fill on the next bar. The order cannot survive its expiry or the regular-session close.

Set `entry1_start_time` and `entry1_end_time` in `[entry1]`. TOML accepts
quoted times such as `"09:30"` or local time literals such as `09:30:00` on
the same five-minute grid.

Exit 3 is enabled by default. One position can stay open across regular sessions
until its protective stop is hit. If a bull candle's high is below either of the
two preceding sessions' daily lower levels, the stop can rise to the candle low
minus half its ATR(20), rounded down to a tick and effective from the next bar.
The report shows any position still open at the replay cutoff.

To run the legacy exits, copy the TOML alongside the original and enable
`same_day_close` or `next_session_open` in `[exits]`. Set `trailing_stop = false`
for a legacy-only run and choose a distinct `--output`. Each enabled exit
compounds independently. If the next verified session is unavailable, the final
signal day is excluded from next-open entries. The report gives the actual
signal range and any exit-only date.

The immediate next NYSE session may be used for an exit even beyond the
published signal range, but only if its cached bars independently pass
verification. It cannot generate entries and is hidden in the same-day view.

The backtest uses whole shares, 1% planned risk and a cash cap by default. It charges modeled IBKR Pro Fixed commissions and configurable adverse slippage. Gap moves and five-minute path ambiguity are modeled separately; planned risk is not a guaranteed maximum loss. The report includes scenario results, equity and drawdown, a price-only buy-and-hold benchmark, session charts, and order and trade logs.

Trade and order-event buttons bring the selected session into view. The chart
labels protective stops, same-day closes, next opens, and ambiguous stop bars.
“Adverse gap vs. trigger/stop” is not total overnight movement: it measures
adverse overshoot at the trigger or stop. All overnight movement is already
reflected in net P&L.

**Profit factor** is total net P&L from winning trades divided by the absolute
net P&L from losing trades, after modeled costs. It is calculated separately
for each exit scenario; `∞` means wins without losses and `—` means no nonzero trades.

### Reproduce the November 24, 2026 run

On a fresh checkout, install the package as described above. Use the archived
inputs for an exact reproduction. To rebuild with current provider data, use
the [dated TWS publisher command](docs/spy-5m-coverage.md#published-uninterrupted-range)
and download Yahoo levels with `python -m qqq_viewer.spy_daily --cache
data/spy_daily_rebuild.json`. Point a copy of `config/SPY.toml` at those two
rebuild files; the commands use separate paths so they do not replace installed
caches. A provider rebuild may differ from the archived inputs.

With the archived inputs at the paths in `config/SPY.toml`, run these from
the repository root. If a report already exists, choose a new output name.

```powershell
$python = ".\.venv\Scripts\python.exe"
$sameDay = "config/SPY_same_day_replay.toml"
$comparison = "config/SPY_exit_comparison_replay.toml"
if ((Test-Path $sameDay) -or (Test-Path $comparison)) { throw "Choose unused replay config paths." }
$legacy = (Get-Content config/SPY.toml -Raw).Replace("same_day_close = false", "same_day_close = true").Replace("trailing_stop = true", "trailing_stop = false")
$legacy | Set-Content -Encoding ascii $sameDay
$legacy.Replace("next_session_open = false", "next_session_open = true") | Set-Content -Encoding ascii $comparison
& $python -m qqq_viewer.backtest_spy_5m --config $sameDay --start 2013-12-24 --end 2026-11-24 --output output/SPY_5m_entry1_2013-12-24_2026-11-24_replay.html
& $python -m qqq_viewer.backtest_spy_5m --config $comparison --start 2013-12-24 --end 2026-11-24 --output output/SPY_5m_entry1_exit_comparison_2013-12-24_2026-11-24_replay.html
```

Expected archived-input coverage: **3,249 signal sessions and 252,450 bars**;
**71 trades** in each exit scenario. Under **Settings and input fingerprints**,
compare these SHA-256 values. The intraday hash covers all consumed bars:
ATR warmup, signal sessions, and any verified exit-only session. It does not
hash the SQLite file bytes:

- Intraday: `37d8a0626c5d9d1ea470bb358dde7713dad60f6828c0dcfa9c864682cc18cc29`
- Daily: `b955d740c3111250be921f84ecbffd72c37fe2fda8c8c255496a9a27918e9577`

New daily snapshots identify themselves as SPY/Yahoo Finance (`yahoo-daily-v1`).
The runner accepts the exact archived legacy daily file by its fingerprint,
without modifying it. Refresh any other unlabeled file to a new path rather
than assuming its ticker or changing its metadata by hand. The archived market
data itself is not in Git.
