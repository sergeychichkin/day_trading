# SPY five-minute Entry 1 VWAP and overnight order

## Approaches

Entry 1 uses VWAP confirmation after the breakout and checks VWAP again at the bear close. A one-time VWAP check would allow an order after VWAP falls back to or below the daily Upper, which the strategy excludes. Carrying the fixed order into the next session lets the open reach the same trigger without recalculating size or price. The report shows that order as a time-bounded chart line.

## Entry 1

- Within the configured New York candle-start window, the first bull close strictly above the daily Upper starts the setup. Wait for session VWAP to be strictly above that level, then use the first bear candle at or after confirmation. VWAP may confirm on the breakout candle or the bear candle. If VWAP is at or below Upper at that bear close, the setup ends for the session.
- At the qualifying bear close, fix the buy stop at the regular-session high through that candle plus the configured multiple of its ATR(20), rounded up to a tick. Use the signal session's Daily Lower, rounded down, for the attached protective stop and preserve the sizing and gap rules.
- The buy stop is eligible from the next regular-session candle. It can fill during the next NYSE session's 9:30 candle and expires before the 9:35 candle can fill it. Use the exchange calendar across weekends, holidays and daylight saving changes.
- A replay ending before the next opening candle reports a still-active order at cutoff. The existing optional exit-only tail is available to close next-open trades. A filled overnight order uses its actual fill date for exit timing. Exit 3 keeps the original stop through the first NYSE session after the fill and applies its existing two-session trailing rule thereafter.

## Report and documentation

Draw a horizontal segment at the fixed buy stop from submission through the fill candle or expiration. At a replay cutoff, draw an unresolved order through the last observed candle. End the segment at a terminal fill-time rejection if one occurs. The order audit includes submission, activation, and the terminal event when observed. Mark the breakout and the fill on their respective sessions.

Shorten the README's Entry 1 and Exit 3 rules, and remove the November 24 reproduction section. The replay and report remain offline and use the current branch and local inputs.
