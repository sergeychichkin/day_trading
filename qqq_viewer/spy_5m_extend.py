"""Extend the published SPY five-minute cache backward using Sierra .scid and TWS."""

import argparse
import os
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from .spy_5m_backfill import STOP, SpyHistory, _by_session
from .spy_5m_contiguous import _identity, _require_distinct, verify_published
from .spy_5m_sierra import read_scid_five_minute_bars
from .spy_5m_snapshot import SQLITE_SIDECARS, read_snapshot
from .spy_5m_store import (
    BAR_FIELDS, DEFAULT_DB, FiveMinuteStore, expected_times, sessions, usable_session,
)

DEFAULT_SCID = Path(r"C:\SierraChart\Data\SPY.scid")
DEFAULT_STAGING = Path("data/spy_5m_sierra_staging.sqlite3")
DEFAULT_FLOOR = date(2008, 5, 5)


def _unlink_with_sidecars(path: Path) -> None:
    for candidate in (path, *(Path(str(path) + suffix) for suffix in SQLITE_SIDECARS)):
        candidate.unlink(missing_ok=True)


def _close_wal(path: Path) -> None:
    with closing(sqlite3.connect(path, timeout=10)) as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("PRAGMA journal_mode=DELETE")


def _copy_tables(target: sqlite3.Connection, schema: str, start: str, end: str) -> None:
    params = (start, end)
    for table, fields in (
        ("bars", BAR_FIELDS + ",date"),
        ("sessions", "date,completed,verified"),
        ("attempts", "date,status,diagnostic,accepted,recorded_at"),
    ):
        target.execute(
            f"INSERT INTO {table} ({fields}) SELECT {fields} FROM {schema}.{table} "
            "WHERE date BETWEEN ? AND ?",
            params,
        )


def _contiguous_pre_window(stage: FiveMinuteStore, pre_window: list) -> tuple[list, date | None]:
    if not pre_window:
        return [], None
    first, last = pre_window[0].date.isoformat(), pre_window[-1].date.isoformat()
    with stage._connection() as db:
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
    blocked = None
    contiguous = []
    for session in reversed(pre_window):
        key = session.date.isoformat()
        expected = len(expected_times(session))
        if key not in verified or key in conflicts or (key, expected) not in complete_counts:
            blocked = session.date
            break
        contiguous.append(session)
    contiguous.reverse()
    return contiguous, blocked


def _stage_pre_history(
    scid_path: Path,
    stage: FiveMinuteStore,
    pre_window: list,
    con_id: int,
    volume_unit: str,
    history_factory,
) -> tuple[int, int]:
    stage.configure(con_id, volume_unit)
    stage.set_target(pre_window[-1].date)

    pending = stage.pending(pre_window)
    sierra_count = 0
    if pending:
        grouped = read_scid_five_minute_bars(scid_path, pending)
        for session in pending:
            bars = grouped.get(session, [])
            if usable_session(session, bars):
                stage.record(session, "complete", bars, "sierra-scid-1m")
                sierra_count += 1

    remaining = stage.pending(pre_window)
    tws_count = 0
    if remaining:
        if history_factory is None:
            raise ValueError("TWS history client is required to patch missing Sierra sessions.")
        with history_factory() as history:
            identity = history.qualify_spy()
            if identity.con_id != con_id:
                raise ValueError(f"SPY contract mismatch: cache uses {con_id}, TWS returned {identity.con_id}.")
            for session in reversed(remaining):
                status, raw_bars, detail = history.request([session], "1 D")
                if status in STOP or status == "timeout":
                    raise ValueError(f"{session.date.isoformat()}: {status}; {detail}")
                mapped = _by_session([session], raw_bars)[session]
                complete = status == "complete" and usable_session(session, mapped)
                stage.record(session, status, mapped, "tws-1d-patch" if complete else detail)
                if complete:
                    tws_count += 1
    return sierra_count, tws_count


def _backup_source(source_db: Path, backup_path: Path) -> None:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with read_snapshot(source_db) as src_db, closing(sqlite3.connect(backup_path, timeout=10)) as dst_db:
        src_db.backup(dst_db)
    _close_wal(backup_path)
    verify_published(FiveMinuteStore(backup_path, read_only=True))


def _promote_database(out_db: Path, source_db: Path, backup_path: Path | None) -> None:
    if backup_path is not None:
        _backup_source(source_db, backup_path)
    for suffix in SQLITE_SIDECARS:
        Path(str(source_db) + suffix).unlink(missing_ok=True)
    try:
        os.replace(out_db, source_db)
    except OSError:
        shutil.copyfile(out_db, source_db)
        _unlink_with_sidecars(out_db)
    verify_published(FiveMinuteStore(source_db, read_only=True))


def extend_spy_5m(
    scid_path: Path,
    source_db: Path,
    out_db: Path,
    staging_db: Path,
    *,
    floor: date = DEFAULT_FLOOR,
    history_factory=None,
    backup_path: Path | None = None,
    promote: bool = False,
) -> dict:
    """Extend a verified TWS SPY five-minute cache backward with Sierra .scid data and TWS gap patches."""
    _require_distinct(source_db, out_db)
    _require_distinct(source_db, staging_db)
    _require_distinct(out_db, staging_db)
    if promote and backup_path is not None and backup_path.resolve().exists():
        raise ValueError(f"Refusing to overwrite existing backup: {backup_path}")
    if not scid_path.is_file():
        raise ValueError(f"Sierra SCID file not found: {scid_path}")

    source_info = verify_published(FiveMinuteStore(source_db, read_only=True))
    source_start = date.fromisoformat(source_info["start"])
    source_end = date.fromisoformat(source_info["through"])
    with read_snapshot(source_db) as src_db:
        con_id, volume_unit = _identity(src_db, "main")

    pre_window = sessions(floor, source_start - timedelta(days=1))
    if not pre_window:
        raise ValueError(f"Extension floor {floor} is not before published start {source_start}.")

    stage = FiveMinuteStore(staging_db)
    sierra_count, tws_count = _stage_pre_history(scid_path, stage, pre_window, con_id, volume_unit, history_factory)
    contiguous_pre, blocked = _contiguous_pre_window(stage, pre_window)
    effective_start = contiguous_pre[0].date.isoformat() if contiguous_pre else source_start.isoformat()

    _unlink_with_sidecars(out_db)
    out_store = FiveMinuteStore(out_db)
    out_store.configure(con_id, volume_unit)
    with read_snapshot(source_db) as src_db, TemporaryDirectory(prefix="spy-extend-src-") as tmpdir:
        snap_copy = Path(tmpdir) / "source.sqlite3"
        with closing(sqlite3.connect(snap_copy, timeout=10)) as copy_conn:
            src_db.backup(copy_conn)
        with out_store._connection(write=True) as db:
            db.execute("ATTACH DATABASE ? AS staged", (stage.path.as_uri() + "?mode=ro",))
            db.execute("ATTACH DATABASE ? AS src", (snap_copy.as_uri() + "?mode=ro",))
            if contiguous_pre:
                _copy_tables(db, "staged", effective_start, contiguous_pre[-1].date.isoformat())
            _copy_tables(db, "src", source_start.isoformat(), source_end.isoformat())
            db.executemany(
                "INSERT OR REPLACE INTO metadata VALUES (?,?)",
                [
                    ("verified_contiguous_start", effective_start),
                    ("verified_contiguous_end", source_end.isoformat()),
                    ("target", source_end.isoformat()),
                    ("requested_through", source_end.isoformat()),
                ],
            )
    _close_wal(out_db)
    verified = verify_published(FiveMinuteStore(out_db, read_only=True))

    if promote:
        _promote_database(out_db, source_db, backup_path)

    return {
        **verified,
        "sierra_sessions": sierra_count,
        "tws_patch_sessions": tws_count,
        "preserved_tws_sessions": source_info["sessions"],
        "blocked": blocked.isoformat() if blocked else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scid", type=Path, default=DEFAULT_SCID)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out-db", type=Path, default=Path("data/spy_5m_extended.sqlite3"))
    parser.add_argument("--staging-db", type=Path, default=DEFAULT_STAGING)
    parser.add_argument("--floor", type=date.fromisoformat, default=DEFAULT_FLOOR)
    parser.add_argument("--port", type=int, default=7497)
    parser.add_argument("--client-id", type=int, default=15914)
    parser.add_argument("--backup", type=Path, default=None)
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()

    try:
        report = extend_spy_5m(
            args.scid, args.db, args.out_db, args.staging_db,
            floor=args.floor,
            history_factory=lambda: SpyHistory(args.port, args.client_id, min_interval=2.5),
            backup_path=args.backup,
            promote=args.promote,
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
        print(f"Blocked before {report['blocked']}; contiguous start is {report['start']}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
