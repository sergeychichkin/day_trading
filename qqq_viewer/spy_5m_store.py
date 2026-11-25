"""NYSE session grid and durable IBKR SPY five-minute cache."""

import math
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import asdict, astuple, dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import pandas_market_calendars as markets

from .sessions import NEW_YORK, Session

START = date(2000, 1, 1)
DEFAULT_DB = Path("data/spy_5m.sqlite3")
STEP = 300
APPLICATION_ID = 0x5350354D
BAR_FIELDS = "time, open, high, low, close, volume, wap"
SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sessions (date TEXT PRIMARY KEY, completed INTEGER NOT NULL, verified INTEGER NOT NULL);
CREATE TABLE attempts (date TEXT NOT NULL, status TEXT NOT NULL, diagnostic TEXT NOT NULL,
                       accepted INTEGER NOT NULL, recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE bars (time INTEGER PRIMARY KEY, open REAL NOT NULL, high REAL NOT NULL,
                   low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL, wap REAL,
                   date TEXT NOT NULL);
CREATE INDEX bars_by_date ON bars(date, time);
CREATE TABLE conflicts (time INTEGER PRIMARY KEY, date TEXT NOT NULL);
CREATE INDEX conflicts_by_date ON conflicts(date, time);
"""


@lru_cache(maxsize=32)
def _schedule(start: date, end: date) -> tuple[Session, ...]:
    if end < start:
        return ()
    rows = markets.get_calendar("NYSE").schedule(start, end)
    return tuple(Session(day.date(), int(row.market_open.timestamp()), int(row.market_close.timestamp()))
                 for day, row in rows.iterrows())


def sessions(start: date, end: date) -> list[Session]:
    return list(_schedule(start, end))


def completed(now: datetime) -> list[Session]:
    if now.utcoffset() is None:
        raise ValueError("A timezone-aware SPY cutoff is required.")
    return [session for session in sessions(START, now.astimezone(NEW_YORK).date())
            if session.close_utc <= now.timestamp()]


def expected_times(session: Session) -> range:
    return range(session.open_utc, session.close_utc, STEP)


@dataclass(frozen=True)
class FiveMinuteBar:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    wap: float | None


def _metadata(db, key):
    row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


class FiveMinuteStore:
    def __init__(self, path: Path, *, read_only: bool = False):
        self.path = path.resolve()
        self.read_only = read_only
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                marker = db.execute("PRAGMA application_id").fetchone()[0]
                if not marker and not db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone():
                    for statement in SCHEMA.strip().rstrip(";").split(";"):
                        db.execute(statement)
                    db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                else:
                    self._validate(db)
                db.commit()
                db.execute("PRAGMA journal_mode=WAL")

    @staticmethod
    def _validate(db):
        if db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise ValueError("Not a SPY five-minute cache.")

    @contextmanager
    def _connection(self, *, write=False):
        if write and self.read_only:
            raise ValueError("The SPY cache is read-only.")
        location = self.path.as_uri() + ("?mode=rw" if write else "?mode=ro")
        with closing(sqlite3.connect(location, uri=True, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            self._validate(db)
            yield db

    def configure(self, con_id: int, volume_unit: str) -> None:
        if con_id <= 0 or volume_unit not in {"shares", "lots"}:
            raise ValueError("A positive SPY contract ID and volume unit (shares or lots) are required.")
        with self._connection(write=True) as db:
            for key, value in (("contract", str(con_id)), ("volume_unit", volume_unit)):
                previous = _metadata(db, key)
                if previous is not None and previous != value:
                    raise ValueError(f"SPY {key} mismatch: cache uses {previous}, not {value}.")
                db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, value))

    def set_target(self, through: date) -> None:
        with self._connection(write=True) as db:
            db.execute("INSERT INTO metadata VALUES ('target',?) "
                       "ON CONFLICT(key) DO UPDATE SET value=max(value, excluded.value)", (through.isoformat(),))

    def record(self, session: Session, status: str, bars: list[FiveMinuteBar], diagnostic: str = "") -> None:
        key = session.date.isoformat()
        grid = expected_times(session)
        accepted = 0
        with self._connection(write=True) as db:
            existing = {row[0]: row for row in db.execute(f"SELECT {BAR_FIELDS} FROM bars WHERE date=?", (key,))}
            for bar in bars:
                values = astuple(bar)
                if (bar.time not in grid or not all(math.isfinite(value) for value in values[1:6])
                        or not 0 < bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high
                        or bar.volume < 0):
                    continue
                accepted += 1
                wap = bar.wap if bar.wap is not None and math.isfinite(bar.wap) and bar.wap > 0 else None
                values = (*values[:6], wap)
                old = existing.get(bar.time)
                if old is None:
                    db.execute("INSERT INTO bars VALUES (?,?,?,?,?,?,?,?)", (*values, key))
                    existing[bar.time] = values
                elif old[:6] != values[:6] or (old[6] is not None and wap is not None and old[6] != wap):
                    db.execute("INSERT OR IGNORE INTO conflicts VALUES (?,?)", (bar.time, key))
                elif old[6] is None and wap is not None:
                    db.execute("UPDATE bars SET wap=? WHERE time=?", (wap, bar.time))
                    existing[bar.time] = values
            db.execute("INSERT INTO attempts(date,status,diagnostic,accepted) VALUES (?,?,?,?)",
                       (key, status, diagnostic, accepted))
            db.execute("INSERT INTO sessions VALUES (?,?,0) "
                       "ON CONFLICT(date) DO UPDATE SET completed=max(completed, excluded.completed)",
                       (key, int(status == "complete" and accepted > 0)))
            valid = len(existing) == len(grid) and all(row[5] == 0 or row[6] is not None for row in existing.values())
            db.execute("UPDATE sessions SET verified=completed AND ? WHERE date=?", (int(valid), key))

    def _verified(self, db):
        return {row[0] for row in db.execute("SELECT date FROM sessions WHERE verified=1")}

    def pending(self, target: list[Session]) -> list[Session]:
        with self._connection() as db:
            verified = self._verified(db)
        return [session for session in target if session.date.isoformat() not in verified]

    def latest_date(self) -> date | None:
        if not self.path.is_file():
            return None
        with self._connection() as db:
            value = self._latest(db)
        return date.fromisoformat(value) if value else None

    @staticmethod
    def _latest(db):
        return db.execute("SELECT MAX(date) FROM bars").fetchone()[0]

    def target_end_date(self) -> date | None:
        if not self.path.is_file():
            return None
        with self._connection() as db:
            value = _metadata(db, "target") or self._latest(db)
        return date.fromisoformat(value) if value else None

    def coverage(self, target: list[Session]) -> dict:
        expected = sum(len(expected_times(session)) for session in target)
        dates = {session.date.isoformat() for session in target}
        with self._connection() as db:
            selected = [row for row in db.execute("SELECT date, COUNT(*), MIN(time), MAX(time) FROM bars GROUP BY date")
                        if row[0] in dates]
            count = sum(row[1] for row in selected)
            conflicts = sum(row[1] for row in db.execute("SELECT date, COUNT(*) FROM conflicts GROUP BY date")
                            if row[0] in dates)
            verified = self._verified(db)
        return {
            "first_bar": min((row[2] for row in selected), default=None),
            "last_bar": max((row[3] for row in selected), default=None),
            "expected": expected, "received": count, "missing": expected - count,
            "unverified_sessions": len(dates - verified), "conflict_count": conflicts,
        }

    def read_month(self, month: str) -> dict:
        first = date.fromisoformat(month + "-01")
        last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if not self.path.is_file():
            return {"target": None, "sessions": []}
        result = []
        with self._connection() as db:
            through = _metadata(db, "target") or self._latest(db)
            if through:
                for session in sessions(max(first, START), min(last, date.fromisoformat(through))):
                    key = session.date.isoformat()
                    verified = db.execute("SELECT verified FROM sessions WHERE date=?", (key,)).fetchone()
                    result.append({
                        "date": key, "open_utc": session.open_utc, "close_utc": session.close_utc,
                        "verified": bool(verified and verified[0]),
                        "conflict_times": [row[0] for row in db.execute(
                            "SELECT time FROM conflicts WHERE date=? ORDER BY time", (key,))],
                        "bars": [asdict(FiveMinuteBar(*row)) for row in db.execute(
                            f"SELECT {BAR_FIELDS} FROM bars WHERE date=? ORDER BY time", (key,))],
                    })
        return {"target": through, "sessions": result}
