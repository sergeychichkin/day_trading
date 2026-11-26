"""Build, extend, or verify the uninterrupted SPY five-minute viewer cache."""

import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path

from .spy_5m_backfill import STOP, SpyHistory, _by_session
from .spy_5m_snapshot import SQLITE_SIDECARS, read_snapshot
from .spy_5m_store import (
    APPLICATION_ID, BAR_FIELDS, DEFAULT_DB, FiveMinuteBar, FiveMinuteStore, completed,
    expected_times, normalize_bar, publication, sessions, usable_session,
)

DEFAULT_STAGING = Path("data/spy_5m_contiguous_audit.sqlite3")
DEFAULT_FLOOR = date(2004, 1, 23)


def _identity(db, schema):
    info = dict(db.execute(f"SELECT key, value FROM {schema}.metadata WHERE key IN ('contract', 'volume_unit')"))
    con_id = info.get("contract", "")
    unit = info.get("volume_unit")
    if not con_id.isdecimal() or int(con_id) <= 0 or unit not in {"lots", "shares"}:
        raise ValueError(f"Missing or invalid SPY contract or volume metadata in {schema} cache.")
    return int(con_id), unit


def _validate_window(db, window, *, schema="main"):
    first, last = window[0].date.isoformat(), window[-1].date.isoformat()
    params = first, last
    conflict = db.execute(f"SELECT date FROM {schema}.conflicts WHERE date BETWEEN ? AND ? LIMIT 1",
                          params).fetchone()
    if conflict:
        raise ValueError(f"{conflict[0]} has conflicting SPY bars in the {schema} cache.")
    verified = {row[0] for row in db.execute(
        f"SELECT date FROM {schema}.sessions WHERE verified=1 AND completed=1 AND date BETWEEN ? AND ?", params,
    )}
    proof = {(day, count) for day, count in db.execute(
        f"SELECT date, accepted FROM {schema}.attempts WHERE status='complete' AND date BETWEEN ? AND ?", params,
    )}
    query = f"SELECT date, {BAR_FIELDS} FROM {schema}.bars WHERE date BETWEEN ? AND ? ORDER BY date, time"
    expected_total = 0
    with closing(db.execute(query, params)) as cursor:
        groups = groupby(cursor, key=lambda row: row[0])
        for session in window:
            key = session.date.isoformat()
            expected = len(expected_times(session))
            stored_day, rows = next(groups, (None, ()))
            if stored_day != key or key not in verified or (key, expected) not in proof:
                raise ValueError(f"{key} has no verified full SPY response in the {schema} cache.")
            bars = [normalize_bar(FiveMinuteBar(*row[1:])) for row in rows]
            if None in bars or not usable_session(session, bars):
                raise ValueError(f"{key} has a missing slot or unusable VWAP input in the {schema} cache.")
            expected_total += expected
        if next(groups, None) is not None:
            raise ValueError(f"Unexpected non-session SPY bars in the {schema} cache.")
    return expected_total


def verify_published(store: FiveMinuteStore, through: date | None = None) -> dict:
    if not store.path.is_file():
        raise ValueError("No published SPY five-minute cache to verify.")
    with read_snapshot(store.path) as db:
        bounds = publication(db)
        if bounds is None:
            raise ValueError("No published uninterrupted SPY range to verify.")
        start, end = map(date.fromisoformat, bounds)
        cutoff = through or end
        if not start <= cutoff <= end:
            raise ValueError(f"Verification cutoff is outside the published SPY range {start} through {end}.")
        window = sessions(start, cutoff)
        if not window or window[-1].date != cutoff or window[0].date != start:
            raise ValueError("Both verification bounds must be NYSE trading sessions.")
        _identity(db, "main")
        edge = db.execute("SELECT MIN(date), MAX(date) FROM bars").fetchone()
        if edge != bounds:
            raise ValueError("Cached SPY rows lie outside the published range or do not reach its endpoints.")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed for the SPY viewer cache.")
        bars = _validate_window(db, window)
    return {"start": start.isoformat(), "through": cutoff.isoformat(), "sessions": len(window), "bars": bars}


def _require_distinct(left: Path, right: Path):
    def database_files(path):
        path = path.resolve()
        return [path, *(Path(str(path) + suffix).resolve() for suffix in SQLITE_SIDECARS)]

    for first in database_files(left):
        for second in database_files(right):
            if first == second or (first.exists() and second.exists() and first.samefile(second)):
                raise ValueError("SPY viewer and staging caches must be different files with separate sidecars.")


def publish(store: FiveMinuteStore, stage: FiveMinuteStore, window, *, previous, requested: date) -> None:
    _require_distinct(store.path, stage.path)
    with store._connection(write=True) as db:
        if publication(db) != previous:
            raise ValueError("Published SPY range changed during the run; retry without replacing it.")
        if previous is None and db.execute("SELECT 1 FROM bars LIMIT 1").fetchone():
            raise ValueError("Viewer cache contains bars without published continuity metadata; choose another --db.")
        if previous:
            if db.execute("SELECT MIN(date), MAX(date) FROM bars").fetchone() != previous:
                raise ValueError("SPY viewer cache does not match the installed coverage bounds.")
            _validate_window(db, sessions(*map(date.fromisoformat, previous)))
        if window:
            start, end = window[0].date, window[-1].date
            first_required = date.fromisoformat(previous[1]) + timedelta(days=1) if previous else start
            if list(window) != sessions(first_required, end) or end > requested:
                raise ValueError("Publication is not a continuous NYSE window ending by the requested cutoff.")
            db.execute("ATTACH DATABASE ? AS staged", (stage.path.as_uri() + "?mode=ro",))
            if db.execute("PRAGMA staged.application_id").fetchone()[0] != APPLICATION_ID:
                raise ValueError("Not a SPY five-minute staging cache.")
            if _identity(db, "main") != _identity(db, "staged"):
                raise ValueError("SPY staging contract or volume unit differs from the viewer cache.")
            # Attaching, validating, and copying in this transaction pins one source snapshot.
            _validate_window(db, window, schema="staged")
            params = start.isoformat(), end.isoformat()
            for table, fields in (
                ("bars", BAR_FIELDS + ",date"),
                ("sessions", "date,completed,verified"),
                ("attempts", "date,status,diagnostic,accepted,recorded_at"),
            ):
                db.execute(f"INSERT INTO {table} ({fields}) SELECT {fields} FROM staged.{table} "
                           "WHERE date BETWEEN ? AND ?", params)
            db.executemany("INSERT OR REPLACE INTO metadata VALUES (?,?)", [
                ("verified_contiguous_start", previous[0] if previous else params[0]),
                ("verified_contiguous_end", params[1]), ("target", params[1]),
            ])
        db.execute("INSERT INTO metadata VALUES ('requested_through',?) "
                   "ON CONFLICT(key) DO UPDATE SET value=max(value, excluded.value)", (requested.isoformat(),))


def _capture_month(stage, history, month, window, *, newest_first):
    pending = stage.pending(window)
    if pending:
        # End the broker window at the last pending session; verified rows are not replaced.
        status, rows, detail = history.request(pending, "1 M")
        if status in STOP:
            raise ValueError(f"{month}: {status}; {detail}")
        for session, bars in _by_session(pending, rows).items():
            stage.record(session, status, bars, detail)
    pending = set(stage.pending(window))
    by_date = {row["date"]: row for row in stage.read_month(month)["sessions"]}
    for session in reversed(window) if newest_first else window:
        key = session.date.isoformat()
        if session in pending:
            status, bars, detail = history.request([session], "1 D")
            if status in STOP or status == "timeout":
                raise ValueError(f"{key}: {status}; {detail}")
            stage.record(session, status, _by_session([session], bars)[session], detail)
            row = next(row for row in stage.read_month(month)["sessions"] if row["date"] == key)
        else:
            row = by_date[key]
        if not row["verified"] or row["conflict_times"]:
            return session.date, f"{len(row['bars'])}/{len(expected_times(session))} bars, " \
                f"{len(row['conflict_times'])} conflicts, verified={row['verified']}"
    return None, None


def acquire(store, stage, history, now, *, through=None, floor=DEFAULT_FLOOR, volume_unit):
    _require_distinct(store.path, stage.path)
    eligible = completed(now)
    if not eligible:
        raise ValueError("No completed NYSE sessions.")
    requested = through or eligible[-1].date
    if requested not in {session.date for session in eligible}:
        raise ValueError("--through must be a completed NYSE trading session.")
    bounds = store.bounds()
    previous = None
    if bounds["published_start"]:
        previous = bounds["published_start"], bounds["published_through"]
        installed = date.fromisoformat(previous[1])
        if requested <= installed:
            return {**verify_published(store, requested), "requested_through": requested.isoformat(), "blocked": None}
        first = installed + timedelta(days=1)
    else:
        if bounds["latest"] is not None:
            raise ValueError("Viewer cache has no published continuity metadata; choose another --db.")
        first = floor
    window = [session for session in eligible if first <= session.date <= requested]
    if not window:
        raise ValueError("No completed sessions within the requested SPY range.")
    contract = history.qualify_spy()
    stage.configure(contract.con_id, volume_unit)
    store.configure(contract.con_id, volume_unit)
    stage.set_target(requested)
    groups = [(key, list(group)) for key, group in groupby(window, lambda session: session.date.strftime("%Y-%m"))]
    blocked = detail = None
    for month, days in reversed(groups) if previous is None else groups:
        blocked, detail = _capture_month(stage, history, month, days, newest_first=previous is None)
        if blocked:
            print(f"{blocked}: cannot verify uninterrupted coverage ({detail}).", flush=True)
            break
        print(f"{month}: {len(days)} sessions verified.", flush=True)
    if blocked:
        window = [session for session in window if (session.date < blocked if previous else session.date > blocked)]
    publish(store, stage, window, previous=previous, requested=requested)
    result = verify_published(store) if window or previous else {"start": None, "through": None, "bars": 0, "sessions": 0}
    return {**result, "requested_through": requested.isoformat(), "blocked": blocked.isoformat() if blocked else None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--staging-db", type=Path, default=DEFAULT_STAGING)
    parser.add_argument("--through", type=date.fromisoformat, help="Fixed NYSE cutoff, YYYY-MM-DD.")
    parser.add_argument("--floor", type=date.fromisoformat, default=DEFAULT_FLOOR,
                        help=f"Earliest date to consider on a fresh build (default {DEFAULT_FLOOR}).")
    parser.add_argument("--verify", action="store_true", help="Verify the local published range without connecting to TWS.")
    parser.add_argument("--port", type=int, default=7497)
    parser.add_argument("--client-id", type=int, default=15912)
    parser.add_argument("--volume-unit", choices=("shares", "lots"))
    args = parser.parse_args()
    if not args.verify and args.volume_unit is None:
        parser.error("--volume-unit is required when downloading; match the TWS setting.")
    try:
        if args.verify:
            report = verify_published(FiveMinuteStore(args.db, read_only=True), args.through)
        else:
            _require_distinct(args.db, args.staging_db)
            store, stage = FiveMinuteStore(args.db), FiveMinuteStore(args.staging_db)
            with SpyHistory(args.port, args.client_id, min_interval=2.5) as history:
                report = acquire(store, stage, history, datetime.now(timezone.utc), through=args.through,
                                 floor=args.floor, volume_unit=args.volume_unit)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"SPY five-minute coverage: {exc}", file=sys.stderr)
        return 1
    if report["through"] is None:
        print("No uninterrupted SPY coverage was published.")
        return 1
    print(f"{'Verified' if args.verify else 'Published'} {report['start']} through {report['through']}: "
          f"{report['sessions']:,} sessions; {report['bars']:,} five-minute bars; no missing or conflicting bars.")
    if report.get("requested_through", report["through"]) > report["through"]:
        print(f"Requested through {report['requested_through']}; blocked at {report['blocked']}. "
              "Later observations remain in staging.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
