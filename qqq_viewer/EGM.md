# Retained daily viewer and data tools

- `server.py` serves the local SPY daily viewer and `/api/spy/daily` on localhost. It reads `data/spy_daily.json` by default and uses the chart library bundled with `equity_backtest`.
- `spy_daily.py` refreshes the SPY Yahoo daily snapshot. It keeps the completed NYSE sessions, OHLCV, EMA20 direction and daily levels.
- `backfill.py`, `store.py`, `sessions.py` and `ws.py` retain QQQ one-minute cache maintenance with the NASDAQ calendar. The data path remains `data/qqq.sqlite3`.
- `spy_5m_backfill.py`, `spy_5m_contiguous.py`, `spy_5m_sierra.py` and `spy_5m_extend.py` retain SPY five-minute data preparation. `price_basis.py` contains the provider price-basis checks. The SPY store and snapshot imports use the shared five-minute implementations in `equity_backtest`.

The generic five-minute replay reads published data directly. See the [SPY acquisition and verification commands](../docs/spy-5m-coverage.md) and the [generic input contract](../docs/five-minute-inputs.md).
