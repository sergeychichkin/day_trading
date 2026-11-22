# Executed Trades Log timing

## Intent

Make it easy to compare when signals occur across trading dates and how long
executed orders take to fill after their signals, for both entries or for one
entry at a time.

## Approaches

- **Chosen: derive both columns in the report.** Each trade already has
  `signal_time` and `entry_time`. Deriving display and sort values in the
  browser keeps the change local to the trade log.
- **Alternative: add derived report payload fields.** This would duplicate
  information and change the payload without a current need outside the log.

## Design

- Keep **Date** separate. Place **Signal Time** before **Entry Time** and
  **Signal → Entry** after it.
- Display **Signal Time** in New York `HH:mm`, ascending or descending by
  local clock time alone. Do not use the date for sorting or as a tiebreaker.
- Display **Signal → Entry** as minutes from signal timestamp to entry
  timestamp (`0 min` for a same-minute fill). Sort by numeric elapsed time.
- Replace **Winners** and **Losers** in the trade log's **Show** dropdown with
  **Entry 1** and **Entry 2**. **All trades** remains the default. Match the
  executed trade's entry type regardless of PnL and preserve the active sort.
  Do not filter the chart, scenario totals, or order selector.
- Keep existing **Entry Time** sorting, default trade order, and row
  selection unchanged. Do not change replay, execution, or payload data.
- Verify in the browser with trades whose clock-time ordering differs from
  date ordering and durations that distinguish numeric from text sorting.
  Verify entry filtering with both winners and losers.

This updates newly generated reports; existing standalone HTML files retain
their embedded assets until regenerated.
