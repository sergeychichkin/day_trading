# SPY five-minute Entry 1 daily Lower stop

Use the entry session's plotted daily Lower as Entry 1's initial stop. The goal is to reduce stop-outs followed by a quick recovery and measure the effect on risk and results.

## Approaches

- **Selected.** Replace the initial stop rule and run one controlled comparison against the existing rule. This answers the whipsaw question and leaves one rule in the backtester.
- **Alternative.** Make both initial stop rules selectable. This supports repeated comparisons but adds a permanent setting for an experiment that can be run once.

## Stop and execution

- Use the same SPY daily Lower the backtest chart plots for the NYSE session containing the signal and entry. It uses that session's open and the ten preceding sessions. Apply the existing daily-level and opening-price checks.
- Round the Lower down to the configured tick and use it directly as the initial stop in all three exit scenarios. Skip new entries with an explicit reason if the Lower is unavailable or the rounded stop is nonpositive or at or above the buy trigger.
- Compute shares using the existing sizing rules and the revised trigger-to-stop distance. Keep the buy trigger, order lifetime, fill and cost assumptions, and each exit scenario's rules. Exit 3 still manages carried positions under its existing trailing-stop rules, including on days when the current Lower is unavailable for a new entry.
- Remove the initial stop's ATR buffer from the bundled configuration and report. Accept the legacy key in existing configuration files with a clear notice that it no longer affects the stop. Keep the ATR buffer used for the buy trigger.

## Comparison and verification

- Replay the existing rule and the daily Lower rule over the same published SPY five-minute date range using the same local inputs, account settings, and execution assumptions. Enable all three exit scenarios in both comparison runs and record the input fingerprints and replay settings.
- For each scenario, count protective-stop exits with at least one later regular-session five-minute bar on the exit date. Count how many of those trades see a later bar's high reach or exceed the original entry trigger. Exclude the stop-out bar because its high could precede the stop. Show the count and rate among eligible stop exits, as well as how many stop exits had no later bar to check. This is a proxy for same-session recovery after a stop-out.
- Compare executed trades, share size, and net P&L by scenario. Verify tick rounding, sizing, missing or invalid Lower handling, and the interaction with carried Exit 3 positions. Regenerate the standard report with its existing exit configuration.
