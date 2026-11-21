import json
import math
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import astuple, dataclass, field
from datetime import date
from pathlib import Path

from .sessions import Session

RawBar = tuple[str, str, str, str, str, str]
BAR_COLUMNS = "time, open, high, low, close, volume"
SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY, session_date TEXT NOT NULL, open_utc INTEGER NOT NULL,
    close_utc INTEGER NOT NULL, status TEXT NOT NULL, diagnostic TEXT NOT NULL,
    request TEXT NOT NULL, recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS attempts_session ON attempts(session_date, status);
CREATE TABLE IF NOT EXISTS raw_bars (
    id INTEGER PRIMARY KEY, attempt_id INTEGER NOT NULL REFERENCES attempts(id),
    time TEXT NOT NULL, open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL,
    close TEXT NOT NULL, volume TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS raw_attempt ON raw_bars(attempt_id);
CREATE TABLE IF NOT EXISTS bars (
    time INTEGER PRIMARY KEY, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
    close REAL NOT NULL, volume REAL NOT NULL, first_attempt INTEGER NOT NULL REFERENCES attempts(id)
);
CREATE TABLE IF NOT EXISTS conflicts (
    time INTEGER NOT NULL REFERENCES bars(time), attempt_id INTEGER NOT NULL REFERENCES attempts(id),
    raw_bar_id INTEGER NOT NULL REFERENCES raw_bars(id),
    PRIMARY KEY(time, raw_bar_id)
);
"""


@dataclass(frozen=True)
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_raw(self) -> RawBar:
        return tuple(map(str, astuple(self)))

    @classmethod
    def from_raw(cls, raw: RawBar, session: Session) -> "Bar | None":
        try:
            time = int(raw[0])
            opening, high, low, close, volume = map(float, raw[1:])
        except (ValueError, OverflowError):
            return None
        if time not in session.minutes or not all(map(math.isfinite, (opening, high, low, close, volume))):
            return None
        if low <= 0 or low > min(opening, close) or high < max(opening, close) or volume < 0:
            return None
        return cls(time, opening, high, low, close, volume)


@dataclass
class Attempt:
    status: str
    raw: list[RawBar] = field(default_factory=list)
    diagnostic: str = ""
    request: dict = field(default_factory=dict)


class Store:
    def __init__(self, path: Path, *, read_only: bool = False):
        self.path = path.resolve()
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript(SCHEMA)

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            return sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True, timeout=10)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def reader(self):
        with closing(self._connect(read_only=True)) as db:
            db.execute("BEGIN")
            yield db

    @contextmanager
    def _writer(self):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def set_target(self, end_date: date) -> None:
        with self._writer() as db:
            db.execute(
                "INSERT INTO metadata VALUES ('target', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=max(value, excluded.value)",
                (end_date.isoformat(),),
            )

    def target_end_date(self, db: sqlite3.Connection | None = None) -> date | None:
        if db is None:
            with self.reader() as reader:
                return self.target_end_date(reader)
        row = db.execute("SELECT value FROM metadata WHERE key='target'").fetchone()
        return date.fromisoformat(row[0]) if row else None

    def require_volume_unit(self, unit: str) -> None:
        with self._writer() as db:
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('volume_unit', ?)", (unit,))
            existing = db.execute("SELECT value FROM metadata WHERE key='volume_unit'").fetchone()[0]
            if existing != unit:
                raise ValueError(f"Volume unit {unit!r} disagrees with cache unit {existing!r}.")

    def record_attempt(self, session: Session, attempt: Attempt) -> None:
        with self._writer() as db:
            attempt_id = db.execute(
                """INSERT INTO attempts(session_date, open_utc, close_utc, status, diagnostic, request)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session.date.isoformat(), session.open_utc, session.close_utc, attempt.status,
                 attempt.diagnostic, json.dumps(attempt.request)),
            ).lastrowid
            existing = {
                row[0]: Bar(*row) for row in db.execute(
                    f"SELECT {BAR_COLUMNS} FROM bars WHERE time>=? AND time<?",
                    (session.open_utc, session.close_utc),
                )
            }
            additions = []
            for raw in attempt.raw:
                raw_id = db.execute(
                    "INSERT INTO raw_bars(attempt_id, time, open, high, low, close, volume) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", (attempt_id, *raw)
                ).lastrowid
                bar = Bar.from_raw(raw, session)
                if bar is None:
                    continue
                previous = existing.get(bar.time)
                if previous is None:
                    values = (*astuple(bar), attempt_id)
                    db.execute("INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?)", values)
                    existing[bar.time] = bar
                elif previous != bar:
                    additions.append((bar.time, attempt_id, raw_id))
            db.executemany("INSERT INTO conflicts VALUES (?, ?, ?)", additions)

    @staticmethod
    def _times(db: sqlite3.Connection, session: Session) -> set[int]:
        return {row[0] for row in db.execute(
            "SELECT time FROM bars WHERE time>=? AND time<?", (session.open_utc, session.close_utc)
        )}

    def missing_minutes(self, session: Session) -> list[int]:
        with self.reader() as db:
            return sorted(set(session.minutes) - self._times(db, session))

    @staticmethod
    def has_completed_response(db: sqlite3.Connection, session: Session) -> bool:
        return db.execute(
            "SELECT 1 FROM attempts WHERE session_date=? AND status='complete' LIMIT 1",
            (session.date.isoformat(),),
        ).fetchone() is not None

    def pending(self, session: Session) -> bool:
        with self.reader() as db:
            return not self.has_completed_response(db, session) or len(self._times(db, session)) != len(session.minutes)

    def bars_between(self, start_utc: int, end_utc: int) -> list[Bar]:
        with self.reader() as db:
            return [Bar(*row) for row in db.execute(
                f"SELECT {BAR_COLUMNS} FROM bars WHERE time>=? AND time<? ORDER BY time",
                (start_utc, end_utc),
            )]
