# SPY five-minute Sierra + TWS extension Goldfish review

Reviewed commits `2e79323..7285a58` on `main` against baseline `08691bf`. Four fresh-context EGM Goldfish reviewers (`egm-codebase-details`, `egm-design`, `egm-comprehension`, `egm-zoom-out`) audited `qqq_viewer/spy_5m_sierra.py`, `qqq_viewer/spy_5m_extend.py`, `tests/test_spy_5m_sierra.py`, `tests/test_spy_5m_extend.py`, downstream consumers, project documentation, and the promoted `data/spy_5m.sqlite3` cache. Findings below are deduplicated and cross-checked against live data.

## Live data verification (`data/spy_5m.sqlite3` and `C:\SierraChart\Data\SPY.scid`)

- `qqq_viewer.spy_5m_contiguous --verify --through 2026-11-24`: `4,670` sessions / `362,856` five-minute bars (`2008-05-05`–`2026-11-24`), `0` conflicts, `0` stray SQLite sidecars.
- Provenance in `attempts`: `1,409` `sierra-scid-1m` sessions + `12` `tws-1d-patch` sessions + `3,281` original TWS attempt rows (`3,249` preserved TWS sessions, backed up at `data/spy_5m_tws_2013_2026.sqlite3`).
- Direct scan of the `1,409` Sierra sessions in `SPY.scid`: `0` non-monotonic records, `0` invalid 1-minute OHLC records, `0` zero-volume 5-minute bars, `0` sessions missing the `09:30` or final minute—**but `25` sessions are missing 1–8 constituent 1-minute bars** (e.g., `2008-10-15` missing 8 minutes; `2008-07-25` and `2011-04-15` missing 4 minutes), masked because every 5-minute bucket had at least one 1-minute bar.

---

## Documentation and reproduction inconsistencies

### G1. High — `README.md` reports stale TWS-only coverage, breaks the Nov 24, 2026 reproduction, and links a moved anchor
- [README.md#L128-L134](../../README.md#L128-L134) still states published coverage is `December 24, 2013 through November 24, 2026; 3,249 sessions and 252,450 bars` and claims session VWAP uses `"IBKR's five-minute weighted prices and volume"` (omitting Sierra's `1,409` typical-price `wap` sessions and `spy_5m_extend`).
- [README.md#L211-L232](../../README.md#L211-L232) instructs running `--config config/SPY.toml --start 2013-12-24 --end 2026-11-24` and checking `intraday_sha256` against `37d8a062...` (`252,450` bars). Because `config/SPY.toml` points to `data/spy_5m.sqlite3` (`2008-05-05`), `backtest_spy_5m.run` hashes and warms up ATR(20), Volume SMA(20), and prior-day lookups across all `362,856` bars instead, changing the fingerprint and initial indicators unless pointed at `data/spy_5m_tws_2013_2026.sqlite3`.
- [README.md#L205](../../README.md#L205) links `[dated TWS publisher command](docs/spy-5m-coverage.md#published-uninterrupted-range)`, but inserting the Sierra section at [docs/spy-5m-coverage.md#L39](../spy-5m-coverage.md#L39) moved those commands below the new heading.

### G2. High — `EGM.md` and `qqq_viewer/EGM.md` omit the Sierra modules and state TWS-only invariants
- [EGM.md#L42-L54](../../EGM.md#L42-L54) describes SPY five-minute acquisition solely as forward TWS staging/refreshing.
- [qqq_viewer/EGM.md#L64-L94](../../qqq_viewer/EGM.md#L64-L94) omits `spy_5m_sierra.py` and `spy_5m_extend.py`, claims `spy_5m_contiguous.py` is the sole supported build/verification command (`#L68`), and claims `"UTC bars retain broker WAP"` (`#L74`).

### G3. Medium — `docs/spy-5m-coverage.md` re-run command fails on the current checkout; spec/plan hold stale session counts and missing CLI/file docs
- [docs/spy-5m-coverage.md#L52-L56](../../docs/spy-5m-coverage.md#L52-L56) gives a `--promote --backup data/spy_5m_tws_2013_2026.sqlite3` command that fails against the installed repo (backup already exists; `data/spy_5m.sqlite3` already starts at `2008-05-05`).
- [Spec](../superpowers/specs/2026-11-28-spy-5m-sierra-extension-design.md#L5) and [Plan](../superpowers/plans/2026-11-28-spy-5m-sierra-extension.md#L5) cite pre-run estimates (`1,408` Sierra / `4,669` total sessions) instead of the observed `1,409` / `4,670` (`362,856` bars), and omit 4dp tick rounding (`round(close, 4)`).
- `README.md` and `docs/spy-5m-coverage.md` omit `spy_5m_extend` CLI flags/preconditions (`--db` must already be published and start after `--floor`, `--backup` is ignored without `--promote`), the `data/spy_5m_sierra_staging.sqlite3` / `data/spy_5m_extended.sqlite3` / `<db>.promoting` file lifecycles, `bars.wap` vs cumulative session `VWAP`, and the `attempts.diagnostic` schema column.

---

## Code bugs, design risks, and gaps

### G4. High — `.scid` reader RAM buffering, missing 1m OHLC/completeness checks, and `num_trades > 0` bar drop (`qqq_viewer/spy_5m_sierra.py`)
- **Unbounded RAM & sparse linear scan (`#L71-L94`):** `buckets` buffers every raw record tuple across `[ordered[0].open_utc, ordered[-1].close_utc)` in RAM before aggregating, risking OOM on multi-year tick windows and scanning intervening years on sparse `window` inputs. Replace with O(1) per-bucket scalar accumulators and per-contiguous-range `_lower_bound` slices.
- **1-minute OHLC validation & `num_trades > 0` (`#L51-L61`, `#L102`):** `_extract_sample` accepts `num_trades == 0 and open_ > 0` without checking `0 < low <= min(open, close) <= max(open, close) <= high` (a single corrupt 1m bar can poison bucket `volume`/`wap` or drop the 5m bar) and silently drops valid aggregated `.scid` bars with `num_trades > 0`. Also verify monotonic `dt_us` during scanning, exclude/tag odd-lot sentinel ticks (`open_ < -1e30`) if inappropriate for OHLC, and set `wap = None` when `total_volume == 0` (`#L101`, matching `normalize_bar`).
- **Partial 5-minute buckets (`#L95-L112`, `spy_5m_extend.py#L105`):** `usable_session` checks only 5-minute slot presence, accepting sessions missing constituent 1-minute bars (observed on **25 live sessions** in `data/spy_5m.sqlite3`).

### G5. High — `staging_db` reuse bugs, partial-TWS conflict lockout, and unchecked `.scid` price basis / `volume_unit` (`qqq_viewer/spy_5m_extend.py`)
- **Stale counts & `.scid` mismatch on `staging_db` reuse (`#L99-L127`, `#L228-L230`):** `staging_db` records no `.scid` identity/floor metadata; re-runs silently reuse stale bars if `--scid` changes and report `Sierra=0` / `TWS-patch=0` instead of querying actual copied `out_db` provenance.
- **Partial TWS response poisons `staging_db` (`#L122-L126`):** Passing incomplete `mapped` bars (`complete == False`) to `stage.record` can trigger `conflicts` on a retry with revised bars, permanently blocking that session. Also `complete` is checked before `stage.record` logs conflicts. Record `mapped if complete else []` and verify `session not in stage.pending([session])`.
- **Missing symbol, `volume_unit == "shares"`, and daily price-basis validation (`#L184-L198`):** `.scid` files carry no ticker or volume-unit metadata; `extend_spy_5m` stamps `contract='756733'` and `source_db`'s `volume_unit` (even `"lots"`) without checking `volume_unit == "shares"` or running `price_basis_mismatch` against `spy_daily.json`. Also add `scid_path` to `_require_distinct`.

### G6. Major — Promotion races, WAL/`ATTACH` hazards, redundant snapshots, and missing TWS batching/retry (`qqq_viewer/spy_5m_extend.py`, `qqq_viewer/spy_5m_snapshot.py`)
- **TOCTOU & sidecar-unlink order (`#L138-L155`, `#L181-L224`):** `_promote_database` does not re-verify `publication(source_db) == (source_start, source_end)` before replacing `source_db` (a concurrent `spy_5m_contiguous` tail update would be overwritten), removes `source_db` sidecars *after* `os.replace` (`#L153-L154`), lacks Windows `PermissionError` retries, allows `--promote` with `backup_path=None`, and builds/verifies `out_db` before checking `if promote and blocked is not None` (`#L221-L223`).
- **WAL `?mode=ro` & double-copy overhead (`spy_5m_extend.py#L199-L206`, `spy_5m_snapshot.py#L36-L43`):** `ATTACH`ing WAL-mode `staging_db` as `?mode=ro` without `_close_wal` (or `read_snapshot` copying `-wal` without `-shm` in `?mode=ro`) risks SQLite read-only errors on non-empty WAL files. `extend_spy_5m` also runs 3–4 snapshots/verifications and overwrites `requested_through` (`#L215`) with `source_end` instead of preserving `max(existing, source_end)`.
- **TWS `"1 D"`-only requests (`#L118-L126`):** Large Sierra gaps hit TWS one day at a time without `"1 M"` batching, `timeout`/`pacing` retries, or early-stop once a session blocks contiguity.
- **Hardcoded local defaults (`#L22-L24`):** `DEFAULT_SCID = Path(r"C:\SierraChart\Data\SPY.scid")` and `DEFAULT_FLOOR = date(2008, 5, 5)` (vs `2004-01-23` in `spy_5m_contiguous.py`).

### G7. Medium — Duplicated helpers, private boundary imports, and test/polish gaps
- `_contiguous_pre_window` (`spy_5m_extend.py#L52-L85`) and `_copy_tables` (`#L38-L49`) duplicate `_validate_window` and `publish` in `spy_5m_contiguous.py` (while `_contiguous_pre_window` omits bar-level validation inside `out_db` assembly), and `spy_5m_extend.py` imports private `_by_session`, `_identity`, `_require_distinct`, and `stage._connection()`.
- Test gaps: no tests for CLI `main()`, `staging_db` resume/partial-TWS conflicts, `STOP`/`timeout`, `EXDEV`, DST/early-close `.scid`, corrupt 1m `high < low` / `NaN`, or `extend_spy_5m` -> `backtest_spy_5m.run` / `spy_5m_view.month_payload` / `spy_5m_contiguous.acquire` integration.
- Minor polish: unused `SC_EPOCH` (`spy_5m_sierra.py#L12`) and `"Blocked before ..."` wording (`spy_5m_extend.py#L267`, vs `"Blocked at ..."`).

---

## Resolution

| Finding | Resolution & Verification |
| --- | --- |
| **G1–G3** | Updated [README.md](../../README.md), [EGM.md](../../EGM.md), [qqq_viewer/EGM.md](../../qqq_viewer/EGM.md), [spy-5m-coverage.md](../spy-5m-coverage.md), and [design spec](../superpowers/specs/2026-11-28-spy-5m-sierra-extension-design.md) with `4,670` / `362,856` coverage, `spy_5m_extend` CLI/file docs, `#tws-only-rebuild` anchor, `wap` vs cumulative `VWAP`, and `data/spy_5m_tws_2013_2026.sqlite3` in the Nov 24, 2026 reproduction steps. |
| **G4** | Replaced per-record `buckets` list buffering in `spy_5m_sierra.py` with O(1) running accumulators and sparse contiguous-span `_lower_bound` slicing; enforced monotonic `dt_us`, per-record `0 < low <= min(o,c) <= max(o,c) <= high` validation, `num_trades > 0` bar support, `wap = None` on zero-volume buckets, and 1-minute RTH completeness (`read_scid_five_minute_details`). |
| **G5** | Added `scid_identity` metadata guard + `--reset-staging` in `spy_5m_extend.py`, withheld incomplete TWS `mapped` bars from `stage.record` to prevent conflict lockouts, queried actual copied DB provenance totals, enforced `volume_unit == "shares"` and 5% `spy_daily.json` price-basis parity, and fetched all `25` partial-minute sessions from TWS (`37` total `tws-1d-patch` sessions). |
| **G6** | Added pre-build `if promote and blocked` abort, `--backup` / `--allow-no-backup` guards, pre-promotion `publication` TOCTOU re-checks, Windows `PermissionError` retries, `close_wal` before `ATTACH` and inside `read_snapshot` for non-empty WALs without `-shm`, month-batched `"1 M"` + retry TWS requests, `max(requested_through, source_end)` preservation, and required `--scid` with auto-detected `--floor`. |
| **G7** | Extracted shared `sqlite_paths` / `unlink_sqlite` / `close_wal` (`spy_5m_snapshot.py`), `by_session` (`spy_5m_store.py`), and `identity` / `require_distinct` / `validate_window` / `copy_window` (`spy_5m_contiguous.py`); added unit & integration tests across `tests/test_spy_5m_sierra.py` (`5` tests) and `tests/test_spy_5m_extend.py` (`6` tests) and re-verified `data/spy_5m.sqlite3` + full `pytest`. |
