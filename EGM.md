# Local equity replay and data tools

## Runtime boundaries

- `equity_backtest` contains the offline five-minute replay, verified history and snapshot helpers, execution math, and standalone report. Its CLI reads the symbol TOML and two matching local caches.
- `qqq_viewer` retains the separate SPY daily viewer and existing QQQ and SPY acquisition commands. See [the retained interfaces](qqq_viewer/EGM.md).
- `data/` stores local caches and acquisition evidence. `output/` stores standalone reports. Neither directory is part of the installed package.

## Invariants and verification

The replay reads a private SQLite snapshot including any committed WAL, validates input identity and session completeness, and records fingerprints. It protects the config, caches and SQLite sidecars from output aliases. It writes a new explicit report or atomically replaces the default report. The daily viewer serves only local snapshot data and assets.

Run `.\.venv\Scripts\python.exe -m pytest -q` from the repository root. See the [runner and report usage](README.md) and [input contract](docs/five-minute-inputs.md).
