# Five-minute equity backtester

Run Entry 1 and its exits against verified local five-minute and daily data for a US-listed stock or ETF. Open the generated standalone HTML for the chart, executed trades, and order audit.

## Setup on Windows

Use Python 3.13 and run these commands from the repository root.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

## Run the replay

The included [SPY config](config/SPY.toml) uses the installed SPY caches. With no dates supplied, the replay uses the full published range. The chart and report work offline. Pass an unused `--output` path to keep a separate report. Omitting `--output` atomically replaces the default `output/<TICKER>_5m_entry1_<start>_<end>.html`.

```powershell
.\.venv\Scripts\python.exe -m equity_backtest.backtest_5m --config config/SPY.toml
.\.venv\Scripts\python.exe -m equity_backtest.backtest_5m --config config/SPY.toml --start 2020-01-01 --end 2020-12-31 --output output/SPY_5m_2020.html
```

For another instrument, copy the SPY TOML to a new file next to it. Replace the four fields below with the ticker, its verified contract ID, and its two local cache paths. `123456` is an example placeholder. The CLI checks both input identities and verifies the intraday coverage before using the selected sessions. See the [local input contract](docs/five-minute-inputs.md).

```toml
ticker = "IWM"
contract_id = 123456
intraday_cache = "../data/iwm_5m.sqlite3"
daily_cache = "../data/iwm_daily.json"
```

The former `python -m qqq_viewer.backtest_spy_5m` entry point also invokes the same runner.

A run starts flat with the configured cash at its first signal session.
The earlier history supplies indicator warmup and prior daily levels.
Moving `--start` into an existing trade's dates starts a fresh replay.

### Entry 1

1. The first bull candle to close above daily Upper in the configured window starts the setup. Wait for session VWAP to be strictly above Upper, then take the first bear candle at or after confirmation. VWAP may confirm on the breakout or bear candle. If VWAP is at or below Upper at that bear close, the setup ends.
2. Place a buy stop at the regular-session high through the bear candle plus the configured ATR(20) buffer, rounded up to the tick. Size the order using the signal session's Daily Lower as the protective stop, rounded down to the tick. A missing or invalid stop prevents submission.
3. The buy stop becomes eligible on the next regular-session bar and expires at 9:35 a.m. New York time the next NYSE session. The next session's 9:30 candle can fill it. The report's cyan buy-stop line ends at the fill candle or expiry. An order still active at the replay cutoff is shown as pending.

Set `entry1_start_time`, `entry1_end_time` and `entry_atr_buffer` under `[entry1]` in the symbol config. The window uses five-minute candle start times in New York and applies to the breakout. TOML accepts `"09:30"` and `09:30:00`.

### Shared stop for Exits 3–5

The default replay carries one position and one protective stop. The highest eligible raise takes effect. Ties go to the lowest exit number, and an equal price keeps the active stop's source. **Exit Reason**, after **Net P&L**, gives `Initial SL` or the rule that set the stop at the fill. The report shows any position still open at cutoff.

- **Exit 3.** From the 9:45 a.m. ET candle on holding days 3–9, use the first bull candle whose high is strictly below either of the prior two valid Daily Lowers and whose low minus half its five-minute ATR(20) raises the stop. Apply the raise on the next regular-session bar, with at most one effective Exit 3 raise per session. Set the last eligible holding day with `[exit3].max_holding_days`. A raise must take effect by that day's close. The active stop protects the position after that.
- **Exit 4.** The third consecutive daily bear candle triggers a stop review at the next NYSE open for a carried position.
- **Exit 5.** A daily EMA20 change from green to red triggers the same review at the next NYSE open for a carried position.

Exits 4 and 5 propose the lowest low of the last four completed daily sessions, including the trigger session, minus 0.25 times the trigger session's daily Wilder ATR(20). Set `lookback` and `atr_multiplier` separately under `[exit4]` and `[exit5]`. All stop proposals round down to the tick. Exit 3 uses Daily Lowers from the Yahoo snapshot and checks available daily and five-minute opens within 5%. The audit shows the source, trigger and effective time of each raise.

Use `trailing_stop`, `exit4` and `exit5` under `[exits]` to toggle the three rules independently.

To run the legacy exits, copy the TOML alongside the original and enable
`same_day_close` or `next_session_open` in `[exits]`. For a legacy-only run,
set `trailing_stop`, `exit4` and `exit5` to `false` and choose a distinct
`--output`. Each legacy comparison and the shared-stop scenario compound independently.
If the next verified session is unavailable, the final
signal day is excluded from next-open entries. The report gives the actual
signal range and any exit-only date.

The immediate next NYSE session may be used for an exit even beyond the
published signal range, but only if its cached bars independently pass
verification. It cannot generate entries and is hidden in the same-day view.

The backtest uses whole shares, 1% planned risk and a cash cap by default. It charges modeled IBKR Pro Fixed commissions and configurable adverse slippage. Gap moves and five-minute path ambiguity are modeled separately. The report shows realized P&L, closed-balance return and drawdown, any open position's unrealized P&L, and a price-only buy-and-hold benchmark. Strategy and benchmark returns exclude dividends. The replay uses stored prices and models no corporate-action adjustments to positions or orders.

The month picker loads the selected month and the next calendar month when
the report contains both. Scroll across their boundary in the same chart.
Volume and its 20-bar simple moving average share an overlay scale in the
price pane. A verified session immediately before publication can supply the
19 preceding SMA bars. ATR starts warming up at publication. The report's
input coverage lists any retained sessions hashed for the SMA or prior-open
checks, their bar counts and roles, and the total number of fingerprinted
bars. The High and Low ATR guides start off. Edit each multiplier in the chart
toolbar. The legend shows the buffer amount and guide price for each enabled
overlay at the selected candle. Set the report defaults under `[chart]`.
Trade and order-event buttons bring the selected session into view. The chart
marks protective stops and their adjustments, same-day closes, next opens,
and ambiguous stop bars. MFE and MAE use the simulated entry fill minus the
original stop as 1 R. For partial bars they use only prices the replay can
place within the holding interval.
“Adverse gap vs. trigger/stop” is not total overnight movement: it measures
adverse overshoot at the trigger or stop. All overnight movement is already
reflected in net P&L.

**Profit factor** is total net P&L from winning trades divided by the absolute
net P&L from losing trades, after modeled costs. It is calculated separately
for each exit scenario; `∞` means wins without losses and `—` means no nonzero trades.

## SPY daily viewer

The separate daily viewer reads the local SPY daily snapshot. To refresh the snapshot from Yahoo Finance and open the viewer, run:

```powershell
.\.venv\Scripts\python.exe -m qqq_viewer.spy_daily
.\.venv\Scripts\python.exe -m qqq_viewer.server --port 8765
```

Open [the SPY daily viewer](http://127.0.0.1:8765/spy). The Month picker jumps to a month and fits its price range. Drag horizontally to pan into adjacent months. A month with only one cached session initially shows just that session; use **Show adjacent sessions** to restore the surrounding chart. The chart shows the close EMA(20) and two levels fixed at each day's open, based on the previous 10 sessions' average percentage moves from open to high and open to low. Volume and its SMA(20) share the lower area. Indicators carry across months and scheduled market closures, but restart their warmup after a missing scheduled session.

The downloader uses Yahoo Finance daily OHLCV without dividend adjustment, from January 2000 through the last completed US regular session. It prints missing NYSE session dates and writes `data/spy_daily.json`. Rerun the downloader to refresh the snapshot, and reload the page. A refresh that is older than the installed snapshot or omits previously cached days is rejected. The footer distinguishes the last cached bar from the date the exchange calendar was checked through, and warns about missing sessions after the last bar.

For a custom snapshot, pass its path to both commands: `qqq_viewer.spy_daily --cache <path>` and `qqq_viewer.server --spy-cache <path>` (each run with `.\.venv\Scripts\python.exe -m`). The viewer reads locally and needs no internet access to display a downloaded snapshot.

**Initial download verification (November 25, 2026):** Yahoo returned 6,765 bars, from January 3, 2000 through November 24, 2026, with no missing NYSE sessions. The live-cache browser smoke check opened both January 2000 and November 2026 and verified the displayed date ranges.

## Existing data and acquisition

The QQQ one-minute SQLite history remains at `data/qqq.sqlite3`. Its existing maintenance command is `python -m qqq_viewer.backfill`. SPY five-minute history and the existing TWS and Sierra commands are documented in the [coverage record](docs/spy-5m-coverage.md). Preparing local inputs for additional symbols is a separate workflow.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
# Install Chromium only if the Windows installation has no Microsoft Edge.
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest -q
```

Charts use the locally bundled [TradingView Lightweight Charts](https://www.tradingview.com/lightweight-charts/) 4.1.1.
