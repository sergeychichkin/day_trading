# Local inputs for the five-minute backtester

The replay uses one US-listed stock or ETF and the NYSE regular-session calendar. Prepare both local files before running it. Use the same uppercase ticker, including share-class punctuation, in the config and both inputs. The config paths are relative to the TOML file.

## Intraday SQLite

Use the five-minute schema defined in [history_5m.py](../equity_backtest/history_5m.py), with SQLite application ID `0x5350354D`. Each database identifies a single instrument in `metadata`.

| Key | Expected value |
| --- | --- |
| `ticker` | The exact configured symbol |
| `contract` | The configured positive integer IBKR contract ID, stored as text |
| `volume_unit` | `shares` or `lots` |
| `verified_contiguous_start` | First published NYSE session, `YYYY-MM-DD` |
| `verified_contiguous_end` | Last published NYSE session, `YYYY-MM-DD`. The prior format uses `target` as the fallback. |

Every replay and warmup session requires a complete five-minute grid. `bars.time` is the UTC Unix timestamp of the candle start, and `bars.date` is its New York session date. Prices must be valid OHLC and positive-volume candles require a usable `wap`. A complete session must have positive volume, a completed and verified `sessions` row, a `complete` attempt with the full expected bar count, and no recorded conflicts. Raw data requires validation and publication before it can be replayed.

The existing SPY config defaults to IBKR contract `756733`. Its matching legacy intraday cache may omit `ticker` metadata.

## Daily JSON

The daily file uses `schema = "yahoo-daily-v1"`, `source = "Yahoo Finance"` and the configured `ticker`, with these fields at the top level alongside the `bars` array. A bar carries a unique `time` (`YYYY-MM-DD`), daily `open`, `high`, `low`, `close`, `volume`, `upper`, `lower` and `ema_direction`. EMA direction uses `up`, `down` and `neutral`. Warmup values can be null. The replay preserves missing-session handling and withholds signals or resets indicator warmup when required values are unusable. The one audited legacy daily file is accepted for SPY by its exact SHA-256 fingerprint.

## Existing SPY acquisition tools

The existing SPY daily, TWS and Sierra commands remain available. See the [SPY five-minute coverage record](spy-5m-coverage.md) and the [23-ETF acquisition workflow](etf-history-acquisition.md).
