# SPY five-minute shared stop for Exits 3–5

## Approach

- **Selected.** Exits 3–5 manage one protective sell stop for one position. Each rule can propose a higher stop. The highest eligible price wins, and the report records the rule that set the active stop.
- **Alternative.** Run each rule as an independent comparison scenario. This measures each rule in isolation and produces a separate position and equity series for each. The requested default is the shared stop.

## Exit 3

- Keep the existing bull-candle condition, prior Daily Lower eligibility and stop formula. Count NYSE sessions from the actual fill, with the fill session as day 1, matching **Days** in the Executed Trades Log. The existing first two sessions remain ineligible for Exit 3 raises.
- The 9:45 a.m. New York candle is the first eligible signal candle. Apply the first strictly higher qualifying stop from the next regular-session bar, with at most one Exit 3 raise generated per NYSE session. A proposal at or below the active stop leaves the day's opportunity available.
- Make the last eligible holding day configurable, with a default of day 9 inclusive. An Exit 3 raise must take effect by the end of the last eligible session. A stop already in force continues to protect the position afterward.

## Exits 4 and 5

- Exit 4 triggers when the third consecutive daily bear candle closes. A bear candle has a close strictly below its open. A fourth consecutive bear candle does not start another trigger.
- Exit 5 triggers when the stored daily EMA20 direction changes from green (`up`) to red (`down`) across consecutive NYSE sessions. Use the same EMA20 color as the daily chart.
- Both rules propose the lowest low of the prior four completed daily sessions, including the trigger session, minus 0.25 times the trigger session's daily Wilder ATR(20). Make the lookback and ATR multiplier configurable per rule. Round the proposed stop down to the configured tick.
- Apply a qualifying raise at the next regular-session open, before evaluating the opening price against the stop. This applies to positions carried into that session. Use consecutive valid NYSE daily bars, and skip proposals with missing inputs or unavailable ATR.

## Shared stop and report

- Start with Entry 1's original protective stop and its existing execution rules. The stop only ratchets upward. At the same effective time, take the highest proposal. For an exact tie between raises, attribute it to the lowest numbered rule. A proposal equal to the active stop leaves its attribution intact.
- Enable all three rules in the standard `config/SPY.toml`, with independent switches. The Exit 3 time window, daily limit and holding-day cutoff apply to Exit 3. The existing legacy comparison exits remain available.
- Move **Exit Reason** directly after **Net P&L**. Show `Initial SL`, `Exit 3`, `Exit 4` or `Exit 5` for a protective-stop closure, based on the active stop's source at the fill. Keep the same-day and next-open labels for their legacy closes. Identify the source on chart stop adjustments and in order events.
- Update the README and config documentation. Verify session counting, same-open proposal priority, gap handling, timing around 9:45 and day 10, and report labels and sorting.
