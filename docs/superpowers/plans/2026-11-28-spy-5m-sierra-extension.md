# SPY Five-Minute Sierra + TWS Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the verified uninterrupted SPY five-minute cache (`data/spy_5m.sqlite3`) backward from `2013-12-24` to `2008-05-05` (`4,669` sessions through `2026-11-24`) by combining Sierra Chart `SPY.scid` 1-minute data with TWS 1-day patches for Sierra's 12 gap sessions.

**Architecture:** `qqq_viewer/spy_5m_sierra.py` binary-searches and aggregates Sierra `.scid` records onto the NYSE five-minute grid. `qqq_viewer/spy_5m_extend.py` stages complete Sierra sessions, fills incomplete sessions from TWS via `SpyHistory`, copies the contiguous pre-history alongside the untouched existing TWS `data/spy_5m.sqlite3` rows into a verified output database, and optionally promotes it with a backup.

**Tech Stack:** Python 3.13, `sqlite3`, `struct`, `ib_async` (`SpyHistory`), `pytest`.

**Spec:** [SPY five-minute Sierra + TWS history extension](../specs/2026-11-28-spy-5m-sierra-extension-design.md)

## Global Constraints

- Target uninterrupted coverage: `2008-05-05` through `2026-11-24` (`4,669` NYSE sessions).
- Preserve existing `2013-12-24`–`2026-11-24` TWS bars, `wap` values, `sessions`, and `attempts` rows unchanged.
- `FiveMinuteStore` identity remains `contract='756733'`, `volume_unit='shares'`, with no rows in `conflicts` and valid `wap` on every nonzero-volume bar.
- 1-minute `.scid` prices (`trades=0, open>0`) round to 2 decimal places (`round(x, 2)`); 5-minute `wap` uses volume-weighted 1-minute typical price `(high + low + close) / 3` (or unweighted mean typical price if zero volume across the 5-minute bucket).
- Record provenance in `attempts.diagnostic`: `'sierra-scid-1m'` for Sierra sessions and `'tws-1d-patch'` for TWS-patched gap sessions.
- Final promoted database at `data/spy_5m.sqlite3` must pass `qqq_viewer.spy_5m_contiguous --verify --through 2026-11-24` with no stray WAL/SHM sidecars, while retaining a pure-TWS backup (`data/spy_5m_tws_2013_2026.sqlite3`).

## Review Focus

- Malformed `.scid` header (bad magic, header size != 56, record size != 40, version != 1, or trailing partial bytes) raises `ValueError` before reading records.
- IEEE-754 `float32` noise (`183.9499969482422`) and Sierra special `Open` flags (quote-only records or tick records with `trades=1` and `open <= 0`) are normalized/handled without creating invalid bars.
- Partial Sierra sessions (e.g., 77 of 78 bars) are discarded from verification in staging so TWS patches the full day cleanly without triggering `conflicts`.
- If TWS fails to patch a gap session or returns a different `con_id`, the builder aborts promotion, leaves `data/spy_5m.sqlite3` untouched, and does not fabricate synthetic bars.
- Promoting over `data/spy_5m.sqlite3` removes stale `-wal`/`-shm` sidecars cleanly and refuses to overwrite an existing `backup_path`.

---

### Task 1: Sierra `.scid` Reader and Five-Minute Aggregator

**Files:**
- Create: `qqq_viewer/spy_5m_sierra.py`
- Test: `tests/test_spy_5m_sierra.py`

**Interfaces:**
- Consumes: `qqq_viewer.sessions.Session`, `qqq_viewer.spy_5m_store.FiveMinuteBar`, `expected_times`, `normalize_bar`, `STEP`
- Produces:
  - `SC_EPOCH: datetime` (`1899-12-30 00:00:00 UTC`)
  - `read_scid_five_minute_bars(scid_path: Path, window: list[Session]) -> dict[Session, list[FiveMinuteBar]]`

- [ ] **Step 1: Write failing tests for `.scid` header validation, binary-search slicing, `float32` rounding, typical-price `wap`, and tick/quote-only handling**

Write `tests/test_spy_5m_sierra.py` with a small `.scid` fixture writer (`56`-byte `SCID` header + `40`-byte `<q4f4I` records):
- `test_rejects_invalid_scid_header_and_truncated_file(tmp_path)`: bad magic, bad record size, and `size - 56` not divisible by `40` each raise `ValueError`.
- `test_aggregates_1m_records_into_five_minute_bars_with_typical_wap(tmp_path)`: writes 1-minute records for two sessions (including `float32` noise like `183.95`, zero-volume minute inside a positive-volume 5m bucket, and out-of-session pre/post-market records); asserts only expected RTH 5-minute bars are returned, `open/high/low/close` match rounded 2dp aggregation, `volume` sums accurately, and `wap` equals `round(sum(((h + l + c) / 3) * v) / sum(v), 6)`.
- `test_handles_tick_and_quote_only_records_and_empty_window(tmp_path)`: ignores quote-only (`trades=0, volume=0, open=0`), aggregates `trades=1` tick records (`open=0.0` or `-1.99900285e37`) using `close` for OHLC and exact trade-weighted `wap`, and returns `{}` for an empty `window`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\pytest.exe tests/test_spy_5m_sierra.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'qqq_viewer.spy_5m_sierra'`

- [ ] **Step 3: Implement `qqq_viewer/spy_5m_sierra.py`**

Implement `read_scid_five_minute_bars(scid_path: Path, window: list[Session]) -> dict[Session, list[FiveMinuteBar]]`:
- Return `{}` immediately if `not window`.
- Open `scid_path` in binary mode, validate `<4sIIH` header (`SCID`, `56`, `40`, `1`) and exact `(file_size - 56) % 40 == 0`.
- Binary-search the sorted `SCDateTimeMS` timestamps for `[window[0].open_utc, window[-1].close_utc)` using `SC_EPOCH = datetime(1899, 12, 30, tzinfo=timezone.utc)`, then stream the bounded byte range in chunks (`2_000_000` records).
- Map each timestamp `ts = int((SC_EPOCH + timedelta(microseconds=dt_us)).timestamp())` to its `Session` and 5-minute bucket `ts - (ts % STEP)` via `owner = {t: s for s in window for t in expected_times(s)}`.
- For 1-minute records (`ntrades == 0` and `o > 0`), round `o, h, l, c` to 2 decimals and compute typical price `(h + l + c) / 3`; for single-trade tick records (`ntrades == 1` and `c > 0` and `(o == 0 or o < -1e30)`), use `c` for `o, h, l, c` and typical price; skip quote-only/unsupported records.
- Aggregate per 5-minute bucket (`open = first`, `high = max`, `low = min`, `close = last`, `volume = sum`, `wap = round(weighted_tp / volume, 6)` when `volume > 0` else `round(mean_tp, 6)`), pass through `normalize_bar`, and sort bars per session by `time`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\pytest.exe tests/test_spy_5m_sierra.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit Task 1**

```powershell
git add qqq_viewer/spy_5m_sierra.py tests/test_spy_5m_sierra.py
git commit -m "feat: add Sierra SCID five-minute bar reader"
```

---

### Task 2: Hybrid Sierra + TWS Cache Extension Builder and CLI

**Files:**
- Create: `qqq_viewer/spy_5m_extend.py`
- Test: `tests/test_spy_5m_extend.py`

**Interfaces:**
- Consumes: `read_scid_five_minute_bars` from `qqq_viewer.spy_5m_sierra`, `FiveMinuteStore`, `BAR_FIELDS`, `sessions`, `usable_session` from `qqq_viewer.spy_5m_store`, `verify_published` from `qqq_viewer.spy_5m_contiguous`, `SpyHistory`, `_by_session`, `STOP` from `qqq_viewer.spy_5m_backfill`
- Produces:
  - `extend_spy_5m(scid_path: Path, source_db: Path, out_db: Path, staging_db: Path, *, floor: date, history_factory=None, backup_path: Path | None = None, promote: bool = False) -> dict`
  - CLI `python -m qqq_viewer.spy_5m_extend` (`--scid`, `--db`, `--out-db`, `--staging-db`, `--floor`, `--port`, `--client-id`, `--backup`, `--promote`)

- [ ] **Step 1: Write failing tests for hybrid stitching, partial-session TWS patching, gap-cutoff safety, contract mismatch guard, and atomic promotion**

Write `tests/test_spy_5m_extend.py` using synthetic `FiveMinuteStore` fixtures + fake `.scid` + fake TWS history:
- `test_extends_published_tws_cache_with_sierra_and_tws_patch(tmp_path)`: seeds `source_db` with verified TWS sessions for `2025-11-26..2025-11-28`, builds `.scid` covering `2025-11-21..2025-11-25` with `2025-11-24` missing 1 bar, mocks `history_factory` to serve `2025-11-24`, runs `extend_spy_5m(..., promote=True, backup_path=...)`; asserts `source_db` passes `verify_published` from `2025-11-21` through `2025-11-28` (`5` sessions), backup matches original TWS DB, `2025-11-26..2025-11-28` retain original TWS `wap` and `attempts.diagnostic`, Sierra days carry `'sierra-scid-1m'`, `2025-11-24` carries `'tws-1d-patch'`, and no `-wal`/`-shm` sidecars remain on `source_db`.
- `test_stops_at_unfillable_gap_and_refuses_promotion_on_contract_mismatch_or_existing_backup(tmp_path)`:
  - When TWS returns `empty` for `2025-11-24`, `effective_start` stops at `2025-11-25` (`blocked='2025-11-24'`) without inventing bars.
  - When `qualify_spy().con_id != 756733`, raises `ValueError` and leaves `source_db` unchanged.
  - When `promote=True` and `backup_path` already exists, raises `ValueError` before modifying `source_db`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\pytest.exe tests/test_spy_5m_extend.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'qqq_viewer.spy_5m_extend'`

- [ ] **Step 3: Implement `qqq_viewer/spy_5m_extend.py`**

Implement `extend_spy_5m(...)` and CLI `main()`:
- Verify `source_db` first with `verify_published`; extract `(source_start, source_end)` and `(con_id, volume_unit)`.
- Build `pre_window = sessions(floor, date.fromisoformat(source_start) - timedelta(days=1))`; require non-empty `pre_window`.
- Stage Sierra bars via `read_scid_five_minute_bars(scid_path, pre_window)` into a fresh `FiveMinuteStore(staging_db)` (`stage.configure(con_id, volume_unit)`, `stage.set_target(...)`), calling `stage.record(s, "complete", bars, "sierra-scid-1m")` **only** when `usable_session(s, bars)` is True (so partial Sierra sessions stay pending for TWS).
- If `pending = stage.pending(pre_window)` is non-empty, require `history_factory`, connect once, assert `qualify_spy().con_id == con_id`, and fetch each pending session (`"1 D"`), calling `stage.record(s, status, _by_session([s], bars)[s], "tws-1d-patch" if status == "complete" else detail)`.
- Walk backward from `pre_window[-1]` to find the contiguous verified prefix boundary (`blocked` if any session is unverified or has conflicts); require at least the day immediately before `source_start` to be verified.
- Assemble `out_db` (fresh `FiveMinuteStore`) by copying `stage` rows for `[effective_start, pre_window[-1].date]` and `source_db` rows for `[source_start, source_end]` inside one transaction, writing `metadata` (`contract`, `volume_unit`, `verified_contiguous_start=effective_start`, `verified_contiguous_end=source_end`, `target=source_end`, `requested_through=source_end`), switching `PRAGMA journal_mode=DELETE` on close so no sidecars remain, and running `verify_published(FiveMinuteStore(out_db, read_only=True))`.
- If `promote=True`: validate `backup_path` does not already exist (if given), copy `source_db` (after closing/checkpointing via `read_snapshot` or direct copy when idle) to `backup_path`, remove any `source_db` `-wal`/`-shm` sidecars, `os.replace(out_db, source_db)` (or `shutil.copyfile` across volumes), and re-verify `source_db` with `verify_published`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\pytest.exe tests/test_spy_5m_extend.py -v`
Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```powershell
git add qqq_viewer/spy_5m_extend.py tests/test_spy_5m_extend.py
git commit -m "feat: add hybrid Sierra + TWS SPY 5m cache extender"
```

---

### Task 3: Execute Live Cache Extension, Update Coverage Docs, and Verify Full System

**Files:**
- Modify: `docs/spy-5m-coverage.md`
- Runtime data (gitignored): `data/spy_5m.sqlite3`, `data/spy_5m_tws_2013_2026.sqlite3`

**Interfaces:**
- Consumes: `qqq_viewer.spy_5m_extend`, `qqq_viewer.spy_5m_contiguous`, `qqq_viewer.backtest_spy_5m`

- [ ] **Step 1: Run the live extension against `C:\SierraChart\Data\SPY.scid` and TWS port `7497`**

Run:
```powershell
.\.venv\Scripts\python.exe -m qqq_viewer.spy_5m_extend --scid "C:\SierraChart\Data\SPY.scid" --floor 2008-05-05 --port 7497 --backup data/spy_5m_tws_2013_2026.sqlite3 --promote
```
Expected: Exits `0`, prints `2008-05-05` through `2026-11-24`, `4,669` sessions, `0` blocked days.

- [ ] **Step 2: Verify published continuity with `spy_5m_contiguous --verify` and run `backtest_spy_5m` over the full `2008-05-05`–`2026-11-24` range**

Run:
```powershell
.\.venv\Scripts\python.exe -m qqq_viewer.spy_5m_contiguous --verify --through 2026-11-24
.\.venv\Scripts\python.exe -m qqq_viewer.backtest_spy_5m --config config/SPY.toml --start 2008-05-05 --end 2026-11-24 --output output/spy_5m_sierra_verify.html
```
Expected:
- `spy_5m_contiguous --verify`: `Verified 2008-05-05 through 2026-11-24: 4,669 sessions; ... five-minute bars; no missing or conflicting bars.`
- `backtest_spy_5m`: succeeds with `0` missing/unverified coverage errors and cleans up temporary `output/spy_5m_sierra_verify.html` if not needed.

- [ ] **Step 3: Update `docs/spy-5m-coverage.md` with exact observed counts, Sierra + TWS provenance, and verification command**

Update `docs/spy-5m-coverage.md` to document the `2008-05-05`–`2013-12-23` extension (`1,408` Sierra 1-minute consolidated sessions + `12` TWS 1-day patches + `3,249` original TWS sessions = `4,669` uninterrupted sessions, exact bar count observed in Step 2, and `wap` calculation note).

- [ ] **Step 4: Run full test suite**

Run: `.\.venv\Scripts\pytest.exe -q`
Expected: All tests pass with `0` failures.

- [ ] **Step 5: Commit `docs/spy-5m-coverage.md`**

```powershell
git add docs/spy-5m-coverage.md
git commit -m "docs: record SPY 5m Sierra + TWS coverage extension"
```
