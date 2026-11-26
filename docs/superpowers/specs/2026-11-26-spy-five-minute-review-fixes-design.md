# SPY five-minute backtest review fixes

The CLI and standalone report should describe the same simulation the runner
actually performed. This is a follow-up to the
[Entry 1 design](2026-11-25-spy-five-minute-backtest-design.md), not a new
strategy. Existing caches, reports, and default output filenames stay intact.

## Approaches

- **Focused fixes (approved):** Preserve the replay and report format while
  correcting misleading chart and CLI behavior, documenting the existing
  input contract, and adding regression tests.
- **Broader redesign (deferred):** Apart from profit factor, new performance
  measures, synchronized charts, parameterized output names, and a
  multi-ticker adapter are out of scope.

## Expected behavior

- A stop active for only one displayed bar is a dot at the exact stop price,
  not a line extended into another candle. Longer stops keep the dashed line.
  Show the bear candle and identify the volume units. Show the configured
  slippage floor without rounding sub-cent values to cents. Remove the sort
  cursor from headers that do not sort. The same-day scenario does not show
  exit-only sessions.
- Show profit factor for each exit scenario: sum of winning trades' net P&L
  divided by the absolute sum of losing trades' net P&L. Use realized P&L
  after modeled costs, ignoring breakeven trades. Display two decimals for a
  finite result, `∞` for gains without losses, and `—` when all trades break
  even or there are no trades.
- Reject invalid explicit output paths before reading replay inputs; keep
  exclusive output creation. Accept quoted or native TOML local times under
  the same five-minute rules and retain CLI override precedence. Share the
  store's published-bounds validation. Distinguish a missing unpublished
  next session from a malformed or conflicting candidate, and handle an
  empty next-session lookup without a traceback.
- Document that the intraday fingerprint includes warmup, signal, and any
  verified exit-only bars. A verified next session beyond the published
  signal range can be used for an exit, never a new entry.

Trade rules, cash sizing, fees, and fill prices do not change. No live
downloads, cache updates, or replacement of existing reports are needed.
