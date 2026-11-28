"""Extend the published SPY five-minute cache backward using Sierra .scid and TWS."""

import argparse
import errno
import os
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta
from itertools import groupby
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep

from .spy_5m_backfill import STOP, SpyHistory
from .spy_5m_contiguous import copy_window, identity, require_distinct, validate_window, verify_published
from .spy_5m_sierra import read_scid_five_minute_details, scid_first_date, scid_identity
from .spy_5m_snapshot import SQLITE_SIDECARS, close_wal, read_snapshot, sqlite_paths, unlink_sqlite
from .spy_5m_store import (
    DEFAULT_DB, FiveMinuteStore, by_session, expected_times, publication, sessions, usable_session,
)
from .price_basis import load_daily_rows, daily_price_pair, price_basis_mismatch
from .spy_daily import DEFAULT_CACHE as DEFAULT_DAILY_CACHE

DEFAULT_STAGING = Path("data/spy_5m_sierra_staging.sqlite3")
SIERRA_DIAGNOSTIC = "sierra-scid-1m"
TWS_PATCH_DIAGNOSTIC = "tws-1d-patch"


def _load_daily_reference(daily_cache: Path | None) -> dict | None:
    if daily_cache is None:
        return None
    daily, status = load_daily_rows(daily_cache)
    if status != "available":
        raise ValueError(f"SPY daily snapshot is {status} at {daily_cache}; cannot verify Sierra price basis.")
    return daily


def _validate_daily_basis(daily: dict | None, daily_cache: Path | None, session, bars) -> None:
    if daily is None:
        return
    key = session.date.isoformat()
    row = daily.get(key)
    pair = daily_price_pair(row) if row is not None else None
    if pair is None:
        raise ValueError(f"{key} has no valid SPY daily reference in {daily_cache} to verify Sierra price basis.")
    opening, close = bars[0].open, bars[-1].close
    if (price_basis_mismatch(opening, close, row)
            or max(abs(opening / pair[0] - 1), abs(close / pair[1] - 1)) > 0.05):
        raise ValueError(f"{key} Sierra prices ({opening}/{close}) fail 5% price basis check against daily ({pair[0]}/{pair[1]}).")


def _contiguous_pre_window(staging_db: Path, pre_window: list) -> tuple[list, date | None]:
    if not pre_window:
        return [], None
    first, last = pre_window[0].date.isoformat(), pre_window[-1].date.isoformat()
    with read_snapshot(staging_db) as db:
        verified = {
            row[0] for row in db.execute(
                "SELECT date FROM sessions WHERE verified=1 AND completed=1 AND date BETWEEN ? AND ?",
                (first, last),
            )
        }
        conflicts = {
            row[0] for row in db.execute(
                "SELECT DISTINCT date FROM conflicts WHERE date BETWEEN ? AND ?",
                (first, last),
            )
        }
        complete_counts = {
            (row[0], row[1]) for row in db.execute(
                "SELECT date, accepted FROM attempts WHERE status='complete' AND date BETWEEN ? AND ?",
                (first, last),
            )
        }
        contiguous = []
        blocked = None
        for session in reversed(pre_window):
            key = session.date.isoformat()
            expected = len(expected_times(session))
            if key not in verified or key in conflicts or (key, expected) not in complete_counts:
                blocked = session.date
                break
            contiguous.append(session)
        contiguous.reverse()
        while contiguous:
            try:
                validate_window(db, contiguous)
                break
            except ValueError:
                # Narrow down to the first valid suffix after any corrupt bar in staging.
                idx = len(contiguous) - 1
                while idx >= 0:
                    try:
                        validate_window(db, [contiguous[idx]])
                    except ValueError:
                        blocked = contiguous[idx].date
                        contiguous = contiguous[idx + 1:]
                        break
                    idx -= 1
    return contiguous, blocked


def _request_with_retry(history, window: list, duration: str, *, max_retries: int = 2, retry_delay: float = 2.0):
    attempt = 0
    while True:
        status, raw_bars, detail = history.request(window, duration)
        if status in {"timeout", "pacing"} and attempt < max_retries:
            attempt += 1
            if retry_delay > 0:
                sleep(retry_delay * attempt)
            continue
        return status, raw_bars, detail


def _stage_pre_history(
    scid_path: Path,
    stage: FiveMinuteStore,
    pre_window: list,
    con_id: int,
    volume_unit: str,
    daily_cache: Path | None,
    history_factory,
    *,
    retry_delay: float,
) -> None:
    stage.configure(con_id, volume_unit)
    scid_sig = ":".join(map(str, scid_identity(scid_path)))
    previous_sig = stage.get_metadata("scid_identity")
    if previous_sig is not None and previous_sig != scid_sig:
        raise ValueError("Staging cache belongs to a different SCID file; pass --reset-staging to rebuild it.")
    stage.set_metadata("scid_identity", scid_sig)
    stage.set_target(pre_window[-1].date)

    pending = stage.pending(pre_window)
    if pending:
        daily = _load_daily_reference(daily_cache)
        details = read_scid_five_minute_details(scid_path, pending)
        for session in pending:
            bars, complete_1m = details.get(session, ([], False))
            if complete_1m and usable_session(session, bars):
                _validate_daily_basis(daily, daily_cache, session, bars)
                stage.record(session, "complete", bars, SIERRA_DIAGNOSTIC)

    remaining = stage.pending(pre_window)
    if not remaining:
        return
    if history_factory is None:
        raise ValueError("TWS history client is required to patch missing Sierra sessions.")
    with history_factory() as history:
        contract = history.qualify_spy()
        if contract.con_id != con_id:
            raise ValueError(f"SPY contract mismatch: cache uses {con_id}, TWS returned {contract.con_id}.")
        months = [(key, list(group)) for key, group in groupby(remaining, lambda s: s.date.strftime("%Y-%m"))]
        for _, month_sessions in reversed(months):
            if len(month_sessions) > 1:
                status, raw_bars, detail = _request_with_retry(
                    history, month_sessions, "1 M", retry_delay=retry_delay,
                )
                if status in STOP:
                    raise ValueError(f"{month_sessions[0].date.strftime('%Y-%m')}: {status}; {detail}")
                if status == "complete":
                    grouped = by_session(month_sessions, raw_bars)
                    for session in month_sessions:
                        bars = grouped[session]
                        if usable_session(session, bars):
                            stage.record(session, "complete", bars, TWS_PATCH_DIAGNOSTIC)
            month_pending = set(stage.pending(month_sessions))
            blocked_in_month = False
            for session in reversed(month_sessions):
                if session not in month_pending:
                    continue
                status, raw_bars, detail = _request_with_retry(history, [session], "1 D", retry_delay=retry_delay)
                if status in STOP or status == "timeout":
                    raise ValueError(f"{session.date.isoformat()}: {status}; {detail}")
                mapped = by_session([session], raw_bars)[session]
                complete = status == "complete" and usable_session(session, mapped)
                stage.record(session, status, mapped if complete else [], TWS_PATCH_DIAGNOSTIC if complete else detail)
                if not complete or session in stage.pending([session]):
                    blocked_in_month = True
                    break
            if blocked_in_month:
                break


def _replace_file(src: Path, dst: Path) -> None:
    for delay in (0.025, 0.05, 0.1, 0.2, None):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            if delay is None or getattr(exc, "winerror", None) not in (5, 32, 33):
                raise
            sleep(delay)


def _backup_source(source_db: Path, backup_path: Path) -> None:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with read_snapshot(source_db) as src_db, closing(sqlite3.connect(backup_path, timeout=10)) as dst_db:
        src_db.backup(dst_db)
    verify_published(FiveMinuteStore(backup_path, read_only=True))


def _promote_database(
    out_db: Path, source_db: Path, backup_path: Path | None, expected_bounds: tuple[str, str],
) -> None:
    with read_snapshot(source_db) as src_db:
        if publication(src_db) != expected_bounds:
            raise ValueError("Published SPY range changed during extension; retry without overwriting it.")
    if backup_path is not None:
        _backup_source(source_db, backup_path)
    for suffix in SQLITE_SIDECARS:
        Path(str(source_db) + suffix).unlink(missing_ok=True)
    try:
        _replace_file(out_db, source_db)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        staged = source_db.with_name(f"{source_db.name}.promoting")
        try:
            shutil.copyfile(out_db, staged)
            _replace_file(staged, source_db)
        finally:
            staged.unlink(missing_ok=True)
        unlink_sqlite(out_db)
    for suffix in SQLITE_SIDECARS:
        Path(str(source_db) + suffix).unlink(missing_ok=True)
    verify_published(FiveMinuteStore(source_db, read_only=True))


def extend_spy_5m(
    scid_path: Path,
    source_db: Path,
    out_db: Path,
    staging_db: Path,
    *,
    floor: date | None = None,
    daily_cache: Path | None = DEFAULT_DAILY_CACHE,
    history_factory=None,
    backup_path: Path | None = None,
    promote: bool = False,
    allow_no_backup: bool = False,
    reset_staging: bool = False,
    retry_delay: float = 2.0,
) -> dict:
    """Extend a verified TWS SPY five-minute cache backward with Sierra .scid data and TWS gap patches."""
    check_paths = [source_db, out_db, staging_db, scid_path]
    if daily_cache is not None:
        check_paths.append(daily_cache)
    if backup_path is not None:
        if not promote:
            raise ValueError("--backup requires --promote.")
        check_paths.append(backup_path)
    require_distinct(*check_paths)

    if promote and backup_path is None and not allow_no_backup:
        raise ValueError("Pass --backup <path> (or --allow-no-backup) when using --promote.")
    if promote and backup_path is not None:
        if any(p.exists() or p.is_symlink() for p in sqlite_paths(backup_path)):
            raise ValueError(f"Refusing to overwrite existing backup: {backup_path}")
    if not scid_path.is_file():
        raise ValueError(f"Sierra SCID file not found: {scid_path}")

    source_info = verify_published(FiveMinuteStore(source_db, read_only=True))
    source_start = date.fromisoformat(source_info["start"])
    source_end = date.fromisoformat(source_info["through"])
    with read_snapshot(source_db) as src_db:
        con_id, volume_unit = identity(src_db, "main")
        req_row = src_db.execute("SELECT value FROM metadata WHERE key='requested_through'").fetchone()
        src_requested = req_row[0] if req_row else source_end.isoformat()
    if volume_unit != "shares":
        raise ValueError(f"Sierra .scid import requires a share-volume SPY cache ('shares'), not '{volume_unit}'.")

    effective_floor = floor or scid_first_date(scid_path)
    pre_window = sessions(effective_floor, source_start - timedelta(days=1))
    if not pre_window:
        raise ValueError(f"Extension floor {effective_floor} is not before published start {source_start}.")

    if reset_staging:
        unlink_sqlite(staging_db)
    stage = FiveMinuteStore(staging_db)
    _stage_pre_history(
        scid_path, stage, pre_window, con_id, volume_unit, daily_cache,
        history_factory, retry_delay=retry_delay,
    )
    close_wal(staging_db)
    contiguous_pre, blocked = _contiguous_pre_window(staging_db, pre_window)
    if promote and blocked is not None:
        raise ValueError(f"Blocked at {blocked.isoformat()}; refusing to promote incomplete extension over {source_db}.")

    effective_start = contiguous_pre[0].date.isoformat() if contiguous_pre else source_start.isoformat()
    requested_through = max(src_requested, source_end.isoformat())

    unlink_sqlite(out_db)
    out_store = FiveMinuteStore(out_db)
    out_store.configure(con_id, volume_unit)
    with read_snapshot(source_db) as src_db, TemporaryDirectory(prefix="spy-extend-src-") as tmpdir:
        if publication(src_db) != (source_start.isoformat(), source_end.isoformat()):
            raise ValueError("Published SPY range changed during extension; retry without overwriting it.")
        if identity(src_db, "main") != (con_id, volume_unit):
            raise ValueError("Source SPY metadata changed during extension.")
        snap_copy = Path(tmpdir) / "source.sqlite3"
        with closing(sqlite3.connect(snap_copy, timeout=10)) as copy_conn:
            src_db.backup(copy_conn)
        with out_store._connection(write=True) as db:
            db.execute("ATTACH DATABASE ? AS staged", (stage.path.as_uri() + "?mode=ro",))
            db.execute("ATTACH DATABASE ? AS src", (snap_copy.as_uri() + "?mode=ro",))
            if contiguous_pre:
                validate_window(db, contiguous_pre, schema="staged")
                copy_window(db, "staged", effective_start, contiguous_pre[-1].date.isoformat())
            copy_window(db, "src", source_start.isoformat(), source_end.isoformat())
            db.executemany(
                "INSERT OR REPLACE INTO metadata VALUES (?,?)",
                [
                    ("verified_contiguous_start", effective_start),
                    ("verified_contiguous_end", source_end.isoformat()),
                    ("target", source_end.isoformat()),
                    ("requested_through", requested_through),
                ],
            )
    close_wal(out_db)
    verified = verify_published(FiveMinuteStore(out_db, read_only=True))

    with read_snapshot(out_db) as out_conn:
        sierra_total = out_conn.execute(
            "SELECT COUNT(DISTINCT date) FROM attempts WHERE diagnostic=? AND date < ?",
            (SIERRA_DIAGNOSTIC, source_start.isoformat()),
        ).fetchone()[0]
        tws_patch_total = out_conn.execute(
            "SELECT COUNT(DISTINCT date) FROM attempts WHERE diagnostic=? AND date < ?",
            (TWS_PATCH_DIAGNOSTIC, source_start.isoformat()),
        ).fetchone()[0]

    if promote:
        _promote_database(out_db, source_db, backup_path, (source_start.isoformat(), source_end.isoformat()))

    return {
        **verified,
        "sierra_sessions": sierra_total,
        "tws_patch_sessions": tws_patch_total,
        "preserved_tws_sessions": source_info["sessions"],
        "blocked": blocked.isoformat() if blocked else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scid", type=Path, required=True, help="Path to Sierra Chart SPY.scid file.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out-db", type=Path, default=Path("data/spy_5m_extended.sqlite3"))
    parser.add_argument("--staging-db", type=Path, default=DEFAULT_STAGING)
    parser.add_argument("--daily-cache", type=Path, default=DEFAULT_DAILY_CACHE)
    parser.add_argument("--no-daily-check", action="store_true")
    parser.add_argument("--floor", type=date.fromisoformat, default=None)
    parser.add_argument("--port", type=int, default=7497)
    parser.add_argument("--client-id", type=int, default=15914)
    parser.add_argument("--backup", type=Path, default=None)
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--allow-no-backup", action="store_true")
    parser.add_argument("--reset-staging", action="store_true")
    args = parser.parse_args()

    try:
        report = extend_spy_5m(
            args.scid, args.db, args.out_db, args.staging_db,
            floor=args.floor,
            daily_cache=None if args.no_daily_check else args.daily_cache,
            history_factory=lambda: SpyHistory(args.port, args.client_id, min_interval=2.5),
            backup_path=args.backup,
            promote=args.promote,
            allow_no_backup=args.allow_no_backup,
            reset_staging=args.reset_staging,
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"SPY five-minute extension: {exc}", file=sys.stderr)
        return 1

    print(
        f"Extended {report['start']} through {report['through']}: "
        f"{report['sessions']:,} sessions; {report['bars']:,} five-minute bars "
        f"(Sierra={report['sierra_sessions']}, TWS-patch={report['tws_patch_sessions']}, "
        f"preserved-TWS={report['preserved_tws_sessions']})."
    )
    if report["blocked"]:
        print(f"Blocked at {report['blocked']}; contiguous start is {report['start']}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
