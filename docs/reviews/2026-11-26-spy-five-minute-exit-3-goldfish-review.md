# SPY five-minute Exit 3 Goldfish review

Reviewed `ff40e0002566cf6062a41ffcefbab2ce6ca9f893` on `main` against the pre-feature baseline `af7647a`. Three independent, fresh-context EGM Goldfish reviewers covered the replay and runner, the standalone report, and the design and project documentation. The review used the Exit 3 specification and implementation plan, the feature diff, local tests, and the existing SPY inputs and report. Findings below are deduplicated.

Code paths and line references below refer to the reviewed revision. See the [current replay and report documentation](../../README.md).

## Reproduced defects

### R1. High — A stop can round up and close the trade early

Exit 3 delegates its round-down step to a shared helper that adds `1e-9` tick units before flooring. With a candle low of `104.00`, ATR of `1.020000000002`, and a `0.01` tick, the candidate is `103.489999999999`. Strict round-down gives `103.48`. The replay instead raises the stop to `103.49`. In a synthetic replay with a following candle low of exactly `103.49` and subsequent lows above `103.48`, this closes a position that would still be open under the specified rule. The replay Goldfish also reproduced the boundary using ATR computed by Wilder updates. The bounded SPY replay did not establish a historical occurrence.

Apply strict tick flooring to the Exit 3 candidate while retaining legacy order rounding. Cover candidates exactly on and just below a tick and verify the resulting trade path. [Design](../superpowers/specs/2026-11-26-spy-five-minute-exit-3-design.md#exit-3-replay) · Exit 3 calculation (`spy_5m_replay.py#L250`) · Rounding helper (`opening_range.py#L106`)

### R2. Medium — The opening volume SMA omits a verified prior session

The runner can verify a retained session immediately before the published range for its prior-open check. Its rolling volume sample still begins at the first published bar. A temporary fixture with 100 shares per bar in the verified prior session and 10 shares on the first published bar returned `null` for that bar's SMA. The expected 20-bar average is `95.5`. This affects retained-history caches and is independent of strategy execution. The installed SPY cache has no retained pre-publication rows.

Seed the volume SMA from the immediately preceding verified bars even when Exit 3 is disabled or the daily level is absent. Keep ATR's existing warmup boundary. Pin the first published SMA with and without retained verified history. [SMA design](../superpowers/specs/2026-11-26-spy-five-minute-exit-3-design.md#chart-and-logs) · Prior validation (`backtest_spy_5m.py#L346`) · SMA loop (`backtest_spy_5m.py#L381`)

### R3. Medium — Stop-adjustment navigation selects the following candle

On December 29, 2025, the chart marker is on the `12:30` New York candle. The order event shows `12:35`, the time that candle closes and the new stop takes effect. **Show event** highlights the `12:35` candle, so the user sees the following candle's OHLC. The payload already records both the triggering candle and the activation time. A closing-bar adjustment can also show “Raised” on Friday while the prior stop remains active until Monday's open.

Expose both times in the event row and send its chart action to the triggering candle. Assert navigation and activation text in separate intraday and weekend cases. Event creation (`spy_5m_replay.py#L257`) · Event navigation (`static/spy-5m-report.js#L115`) · [Report](../../output/SPY_5m_exit3_2025-09-02_to_2025-12-31.html)

## Reporting and documentation gaps

### R4. Medium — The headline metrics need a closed-balance label

The replay through December 30 showed `$100,000.00` ending equity and `$0.00` net P&L alongside a terminal unrealized gain of `$1,029.77`. The December 31 run showed `0.00%` max drawdown and a closed trade with `0.40 R` MAE. These results follow the approved closed-balance calculation. General labels such as “Ending equity,” “Return” and “Max drawdown” can be read as including changes in open positions. Qualify the summary and equity chart as closed-balance or realized, and keep the terminal mark separate. Summary labels (`static/spy-5m-report.html#L22`) · [Cutoff design](../superpowers/specs/2026-11-26-spy-five-minute-exit-3-design.md#exit-3-replay)

### R5. Medium — The report cannot reconcile all bars in its intraday fingerprint

An independently verified retained prior session contributes every bar in that session to `intraday_sha256`. The report coverage counts only the ATR warmup, signal range, and exit-only tail. The provenance panel does not list the extra prior sessions or their bar counts. Add their dates, counts, and role in verification to coverage. For a future SMA warmup fix, identify any extra volume input and count each retained session once in the fingerprint. The archived legacy reproduction's existing fingerprint is a separate reference case. Fingerprint input (`backtest_spy_5m.py#L346`) · Coverage output (`backtest_spy_5m.py#L452`) · Provenance panel (`static/spy-5m-report.js#L346`)

### R6. Medium — Bounded replay and overnight cash assumptions need explicit wording

- A bounded run starts flat with configured cash. Earlier sessions warm indicators and supply prior levels. A replay starting December 22 does not carry the December 19 entry from the September–December run. State the flat-start assumption beside `--start` and distinguish indicator continuity from position continuity. [Invocation](../../README.md#spy-five-minute-entry-1-backtest) · Warmup boundary (`backtest_spy_5m.py#L381`)
- Strategy P&L is computed from entry and exit prices and modeled costs. The replay does not credit dividend cash or adjust working orders for corporate actions. The HTML mentions dividend exclusion only for the benchmark. With an overnight position, a dividend-related raw price move can affect the stop result without any offsetting dividend cash in the model. State the strategy's price-only treatment beside the benchmark's. Replay accounting (`spy_5m_replay.py#L275`) · Report cost disclosure (`static/spy-5m-report.html#L75`)

### R7. Low — Distinguish the daily snapshot level from the separate viewer's display

Exit 3 takes the lower from the daily snapshot and compares opens when a verified five-minute open is available. The separate five-minute viewer also checks the close and can suppress a level the backtest uses. Clarify which level and guard apply in the current README and EGM blueprint. The report already embeds both prior lookup rows. Their dates, prices and status could be shown with an adjustment so readers can reproduce it from the report. Link the historical Entry 1 specification to the current Exit 3 design, since the older document describes the removed CLI flags and legacy exit defaults. Backtest guard (`backtest_spy_5m.py#L160`) · Viewer guard (`spy_5m_view.py#L67`) · Static EGM blueprint (`static/EGM.md#spy-five-minute-entry-1-report`) · [Historical design](../superpowers/specs/2026-11-25-spy-five-minute-backtest-design.md)

### R8. Low — Browser tests stop short of exercising visibility and scrolling

Existing tests assert checkbox state and ATR series values, plus data from both months. They do not assert that a plotted series changes visibility or drag the viewport across the boundary. Both passed the Goldfish's offline Edge probes. Add assertions for visible series state after toggling and a viewport crossing that keeps the selected start month. Browser tests (`browser/test_spy_5m_report.py#L175`)

## Verification and attribution

- **Replay Goldfish** — R1 and R2. Differential check against `af7647a` matched existing trade fields, events, and skip reasons in 800 deterministic legacy simulations, including 166 fills. The Friday-to-Monday replay verified pending-stop activation before a gap through the stop and MFE/MAE risk normalization from entry through exit.
- **Report Goldfish** — R3, R4 and R8. Offline Edge loaded 3,126 candles in the November–December window. Dragging into December preserved November as the selected start month. The volume histogram and SMA shared their scale, and both ATR controls changed line visibility. The newer standalone report embeds the JavaScript reviewed here.
- **Design Goldfish** — R2 and R4–R7. Compared the configuration, historical and current specifications, README, EGM blueprints, tests and observed report results.
- **Focused verification** — 66 replay, runner and browser tests passed. Independent probes reproduced R1–R3 and both reported bounded-run behaviors. The effective September 2–December 31, 2025 replay used 85 signal sessions and 6,558 signal bars, closed one trade and recorded three raises. It reproduced intraday fingerprint `be5ac482622cd90f7374b2e5740341bf912b60e126917fa32ac9a83770f29b4f` and daily fingerprint `b955d740c3111250be921f84ecbffd72c37fe2fda8c8c255496a9a27918e9577`.

## Resolution

| Finding | Change and verification |
| --- | --- |
| R1 | Exit 3 floors the raw stop to whole ticks with exact arithmetic over the inputs' decimal representations. Regression cases exercise an exact tick, a candidate just below it and an ATR small enough to disappear in ordinary subtraction. |
| R2 | The 19-bar volume seed comes from the immediately preceding independently verified session, regardless of exit mode or daily level. Tests cover a valid seed, an intervening gap, and ATR's original publication boundary. |
| R3 | Each stop event carries its adjustment. The event shows the triggering candle, effective time and prior daily levels, and its chart action lands on the trigger. Browser tests cover intraday and weekend activation. |
| R4 | The summary and chart identify closed balances, realized P&L and closed-trade costs. The terminal unrealized mark remains visible separately when a position is open. |
| R5 | Input coverage lists each retained session once with its date, bar count, reason for inclusion and number of SMA seed bars. It also reports the exit-only bar count and the total used for the fingerprint. |
| R6 | The README, design and report describe the flat-start and price-only assumptions. The runner test confirms that restarting on Monday preserves that session's indicator data while beginning with configured cash and no carried Friday trade. |
| R7 | Current docs identify the daily snapshot's lower and the backtest's opening-price guard. The historical design links forward. The event details display the prior lookup dates, prices and statuses. |
| R8 | The browser suite checks each ATR series' actual visibility and drags the chart across a month boundary while preserving the month selection. |

The [regenerated report](../../output/SPY_5m_exit3_2025-09-02_to_2025-12-31_audited.html) was checked offline in Edge. It contains one closed trade, three adjustments and 234,822 fingerprinted bars. The December 29 stop event opens the 12:30 candle and states that the stop applies at 12:35. The input hashes matched before and after regeneration. The two legacy scenarios each reproduced 71 trades and the archived input fingerprints. The full pytest suite passed all 251 tests and emitted one `ib_async` event-loop deprecation warning.
