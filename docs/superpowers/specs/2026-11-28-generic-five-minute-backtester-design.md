# Generic five-minute equity backtester and viewer cleanup

## Intent and scope

Use the standalone five-minute backtest report as the intraday chart. Remove the one-minute QQQ backtester and viewer and the separate SPY five-minute viewer. Run the existing five-minute strategy against verified local inputs for a single US-listed stock or ETF per invocation. Data download, source conversion and cache publication for new symbols belong to a separate task.

## Approaches

- **Selected.** Generalize the offline replay, configuration and report together with the viewer cleanup. Define the verified local input contract and test it with synthetic stock and ETF caches. Preserve the existing SPY inputs and acquisition workflows.
- **Alternative.** Generalize Yahoo, TWS and Sierra preparation in the same change. This would provide fresh data for new symbols immediately and would also require changes across provider qualification, price-basis checks, and publication. The user chose to defer data preparation.

## Remove the unused viewers and one-minute backtester

- Remove the one-minute opening-range strategy, CLI, standalone report code, one-minute viewer routes and assets, dedicated config, and feature-specific tests and documentation. Extract the existing `WilderATR`, commission and tick-rounding behavior required by the five-minute replay before deleting the old strategy module.
- Remove the SPY five-minute online viewer routes and assets and the helper functions, tests and docs used exclusively by that viewer. Keep the five-minute standalone report, including its chart, and the separate SPY daily viewer and its routes.
- Keep the QQQ one-minute SQLite data and the existing reader/writer and acquisition command used to maintain it. Keep the existing SPY five-minute and daily inputs, acquisition tools and coverage evidence. Split mixed tests and docs so acquisition and shared-use coverage survive. Remove obsolete viewer entry points and links from maintained documentation and package assets.
- Record the SHA-256 of `data/qqq.sqlite3` and any existing durable WAL before and after implementation without opening the cache through SQLite. Preserve existing reports and all local data files during implementation and verification. Carry forward the working `config/SPY.toml` entry ATR buffer of `0.3`.

## Offline replay and reports

- Expose `python -m equity_backtest.backtest_5m --config <symbol-config.toml>` as the generic CLI with the current date-range and output options. Move replay logic, report code and assets, and provider-independent shared primitives behind generic names. Retain a thin forwarder for the existing `python -m qqq_viewer.backtest_spy_5m` command. Leave existing download command names and data formats available.
- Keep the strategy as configured today, including regular-session replay on the existing NYSE calendar, Entry 1, Exits 3–5, and optional legacy five-minute comparisons. Derive report titles, input coverage, output filenames and diagnostics from the configured ticker. Preserve verification, input fingerprints, volume units and execution assumptions. For ordinary CLI runs, the default report path can be replaced atomically and an explicit `--output` requires a new path. The existing SPY config keeps its paths and works with both CLIs.
- A symbol config identifies the ticker and two local cache paths. Use the same ticker spelling in all three, including any share-class punctuation. Require a positive `contract_id` for non-SPY configurations. The intraday SQLite cache must identify that contract and the same ticker in metadata and use the existing verified five-minute schema with `shares` or `lots` volume. Compare ticker identity across the config, intraday metadata and daily snapshot. Reject mismatched or unknown identity and incomplete intraday verification. The existing SPY intraday cache can omit ticker metadata if its stored contract is `756733`. A present conflicting ticker is an error.
- A daily JSON snapshot must identify `yahoo-daily-v1`, the configured ticker and `Yahoo Finance`. Apply the current daily validation and missing-value behavior to all tickers. Preserve null warmup values, missing-session handling and suppression of signals that lack usable daily inputs. Retain the exact approved legacy daily fingerprint exception for SPY.
- The generic runner consumes read-only inputs. The later acquisition work can produce compatible files using the documented schema and identity fields. This change supplies a symbol config example and an input contract, with no automatic downloads, cache migrations or replacement of existing inputs.

## Verification

- Replay SPY before and after under the same working configuration and date range. Compare trade, event and equity payloads, coverage and fingerprints. Changes to presentation and module naming follow the generic CLI and report conventions.
- Run the normal CLI and generate a standalone chart for synthetic verified stock and ETF fixtures. Test mismatched contract IDs, mismatched daily and intraday tickers, missing identity, raw/unverified inputs, and existing SPY compatibility. Use the offline chart browser tests to preserve report controls and chart overlays.
- Verify retired routes and assets are absent, the SPY daily viewer still works, and acquisition tests and documented commands remain available. Run the full remaining suite, check links to retained docs, and verify the QQQ data hash.
