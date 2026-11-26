# SPY five-minute Exit 3 session lookback

The revised rule gives a new position its original protective stop on the entry session and introduces the daily lower levels in the sessions that follow. This supersedes the lookback and entry-bar eligibility in the [original Exit 3 design](2026-11-26-spy-five-minute-exit-3-design.md).

| Session for this position | Prior daily `lower` prices eligible for tightening |
| --- | --- |
| Session of the actual entry fill | None |
| First NYSE session after the fill | The entry session's `lower` |
| Second NYSE session after the fill and later | Either valid `lower` from the two immediately preceding sessions |

The original stop protects the position from the fill, including on the entry day and at the next regular-session open. Count trading sessions across weekends, exchange holidays and early closes. If the entry session's level is unavailable on the first following session, the active stop stays in force. In later sessions, use any valid level from the two specified dates. A same-session re-entry after a carried exit begins a new schedule.

A surviving bullish candle qualifies when its high is strictly below an eligible level. Raise the stop to the candle low minus half its completed-bar ATR(20), floored to the configured tick, if that price is above the active stop. The increase applies from the next regular-hours bar. Each stop-adjustment event shows the eligible session lookup results and their statuses.

Use the existing ordered NYSE session dates and compare the carried position's entry date with the previous session's date. The alternative is to store and increment position age in the replay state. The date comparison relies on the same session identity used for the daily lookup and avoids a separate counter.

Verify a protected entry-day stop-out, an entry-day candle that would previously have tightened, a holiday or weekend crossing, a missing first-session level, the two-level rule from the second subsequent session, and a same-day fresh entry after a carried exit. Check the report's eligible levels for a stop adjustment.
