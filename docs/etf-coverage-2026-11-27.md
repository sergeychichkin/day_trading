# ETF history coverage through November 27, 2026

The run requests dates from January 1, 2007. The Yahoo daily acquisition is complete for all 23 ETFs. TWS acquisition and final publication are in progress. The latest complete audit will account for all requested NYSE sessions, including the sessions TWS could not supply.

## Yahoo daily coverage

| ETF | First bar | Last bar | Bars | Missing after first bar |
| --- | --- | --- | ---: | ---: |
| QQQ | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| DIA | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| XLF | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| XLK | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| XLE | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| XLI | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| XLP | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| XLY | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| XLV | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| XLU | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| XLB | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| IYR | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| IWM | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| TLT | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| TIP | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| EMB | 2007-12-19 | 2026-11-27 | 4,765 | 0 |
| PFF | 2007-03-30 | 2026-11-27 | 4,948 | 0 |
| IBB | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| EEM | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| GLD | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| SLV | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| GDX | 2007-01-03 | 2026-11-27 | 5,008 | 0 |
| USO | 2007-01-03 | 2026-11-27 | 5,008 | 0 |

Yahoo has no PFF bars for March 26–29, 2007, the first four NYSE sessions from its documented Nasdaq listing. EMB’s first Yahoo bar matches its December 19, 2007 Nasdaq listing. The audit also retains December 17–18 for EMB because the request scope uses fund inception. [PFF listing information](https://www.ishares.com/uk/professionals/en/products/239826/ishares-preferred-and-income-securities-etf-pff) · [EMB listing information](https://www.ishares.com/uk/professionals/en/products/239572/ishares-jp-morgan-usd-emerging-markets-bond-etf)

## Evidence

The provider archive, daily data and acquisition logs are under `data/etf_history/etf_2007_to_2026-11-27/`. The share-volume setting was corroborated against the existing verified QQQ one-minute archive. Total volume matched exactly for the five sessions from November 16 through November 20, 2026.

The interim TWS audit verified all 478 NYSE sessions per ticker from January 2, 2025 through November 27, 2026. All 23 ETFs had zero missing five-minute slots in this interval. [Interim per-ticker audit](../data/etf_history/etf_2007_to_2026-11-27/progress-2025-2026/symbols.csv)

See the [acquisition and audit workflow](etf-history-acquisition.md).
