# Opening-range Entry 1 and Entry 2 design

## Purpose

Add a second QQQ entry pattern and test how lower-priced buy-stop replacements affect both entries. Support several sequential trades in a regular session. Use the existing regular-session minute cache, chart VWAP calculation, and exit rules.

## Approaches

- **Evaluate signals inside the replay.** The user chose this approach. One chronological minute loop checks both entry rules and manages the working buy stop and any open position. This keeps the timing decisions together and supports an order event log for research.
- **Detect signals before replay.** Generate a timestamped candidate stream and let a separate loop execute it. This permits independent candidate inspection and requires a separate candidate interface and a replay to reconcile events.

## Entry rules

- **Entry 1** is the existing opening-range pattern. The range covers the 09:30–09:44 candles. From 09:45 through 11:59, a completed bullish candle qualifies when its low is strictly above the opening-range high. The existing levels and risk defaults apply to each candidate. The replacement and re-entry rules below apply to all new runs, including an Entry 1-only run.
- **Entry 2** examines the 09:34–11:59 closed candles in New York time. A bearish signal candle must be immediately preceded by four consecutive bullish one-minute candles in the same session. Bullish means close greater than open and bearish means close less than open. A doji breaks the streak. A longer bullish streak qualifies using its final four candles. The 09:30–09:33 candles can establish the earliest signal.
- For Entry 2, the default buy stop is the highest high of the signal candle and previous four bars plus 0.25 times the signal-close Wilder ATR(20) on one-minute bars. Its initial protective stop uses the lowest low over the signal candle and previous four bars minus 0.25 times that ATR. Entry and stop level lookbacks and ATR multiples are configurable separately per entry. The signal pattern always checks four preceding bullish candles. The existing ATR calculation carries across regular sessions.
- Round entry triggers up and protective stops down to the configured tick before comparing prices or sizing. Each entry defaults to risking 0.25% of the current realized account balance, measured by the proposed entry-to-stop distance. Round shares down to whole units. Default account and leverage remain $100,000 and 4×. Apply the buying-power cap at the trigger when sizing a working order. Reduce the fill quantity if the modeled fill price would exceed that cap. If a triggered order can afford zero shares, clear it and log the skipped fill. The completed candle may then qualify for a fresh order. Log unusable levels, insufficient warmup, or zero-share candidates as skipped events.

## Minute replay

- At most one buy stop can be working, and only while flat. A valid and affordable signal places a stop if none is working. It replaces an existing stop only if its rounded buy trigger is strictly lower. Recompute the initial protective stop and shares entirely from the replacement signal and its entry configuration. An equal or higher trigger leaves the working order in place and is logged as a skipped candidate. Entry 1 signals are bullish and Entry 2 signals are bearish, so the same closed candle cannot produce both.
- Process prior working orders and protective stops using the current minute's prices before processing that minute's completed candle. A newly placed or replaced order becomes active on the next minute. An existing stop-entry order can fill during the candle that later produces a replacement candidate. Preserve the adverse same-minute entry-and-stop convention and record ambiguity.
- Honor marketable entry gaps and regular-session timing under the existing fill model. A previously working order can fill during the 11:59 minute. Cancel unfilled orders at noon before processing the 12:00 minute. A signal from the 11:59 candle closes at noon, so log a skipped candidate with no active window.
- An open position uses its own initial protective stop and the same exit logic for both entry types. If a completed bearish candle has a high strictly below the current session VWAP estimate, raise the stop to the lowest low of that candle and the configured previous bars minus the shared ATR offset when that raises the working stop. Apply the update on the next minute.
- Use the 13:00 minute open for scheduled liquidation and the existing final-session-close proxy on an early-close day. Once an exit leaves the account flat, its candle may qualify at the close if it is in the applicable signal window. Permit further sequential fills until noon. Skip entry evaluation at the close when a position remains open. Reset all orders and position state at the end of each session.

## Settings and reports

- In each ticker TOML, keep account, leverage, tick, shared trailing-stop settings and costs at the ticker level. Give Entry 1 and Entry 2 separate tables for enable flags, level lookbacks, ATR offsets and risk percentage. Migrate the existing QQQ settings and default both entries to enabled for the comparison. Preserve compatibility with the current flat settings format by mapping its entry parameters to Entry 1 and leaving Entry 2 disabled for flat configurations.
- Keep inclusive --start and --end with full calendar 2020 as the default. When both entries are enabled, replay Entry 1 only, Entry 2 only, and both using identical dates and independent starting balances. When one is enabled, replay that entry only. Reject a run with both disabled. Preserve the existing broker commission model and base and double fill-slippage scenarios. Run history locally from the same verified bar snapshot.
- Preserve the original 2020 HTML baseline at output/QQQ_opening_range_2020-01-01_2020-12-31.html. Generate new comparison output under a distinct default filename. Identify the changes in the new Entry 1-only run so readers can compare it to the original one-signal-per-day baseline.
- Show comparison results, with trades and charts selectable for each entry combination and each slippage assumption. Keep the established 12 Executed Trades Log columns and order. In chart markers and selected-trade details, show the entry type, signal candle, initial stop, fills, and the active stop path. Keep charts for orders that were replaced or expired and show their active windows. Store placed, replaced, skipped, filled and canceled events in the report's embedded data with times, entry type, and relevant price levels and shares. Attribute a fill to the order active at that moment.
- If viewing several trades from the same day, zoom to the selected trade and use its own order and stop path. Equity and reported trade metrics use the selected run, even when several rows share a date. Record realized balance at each exit and at session close. Calculate closed-balance drawdown after every exit, using starting equity as the initial high-water mark. The equity chart includes each exit so a same-day recovery cannot hide an earlier drawdown.

## Acceptance

- Exercise Entry 2 at 09:34 with the first four regular bars, a longer bullish streak, a doji, and the noon cutoff.
- Check lower-price replacement across entries and within the same entry, equal or higher triggers, replacement after a completed signal, and fill precedence before candle close.
- Check two sequential fills in one session and prove that no position or working order overlaps a long position. Verify stop updates, gap fills, adverse same-minute exits, and the 13:00 or early-close exits.
- Generate and verify the 2020 comparison using the current cache. Reconcile results and executed trades for each scenario, exercise enable switches, and verify chart navigation and the original baseline SHA-256 56f73f4805be15ce055d20a0a0f1f4261b5077a015cc5eedeb37b1c55f3d131d.
- Keep research outputs and live order handling separate. The existing backtest data, account and costs are the inputs for this change.
