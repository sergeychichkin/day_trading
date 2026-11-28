"""Compatibility entry point for the original SPY five-minute CLI."""

from equity_backtest.backtest_5m import main


if __name__ == "__main__":
    raise SystemExit(main())
