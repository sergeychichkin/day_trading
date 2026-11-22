import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from itertools import groupby
from pathlib import Path
from statistics import median
from time import sleep
from typing import Protocol

from .sessions import NEW_YORK, START_DATE, Session, completed_sessions
from .store import Attempt, Bar, Store
from .tws import ContractIdentity, TwsHistory

DEFAULT_DB = Path("data/qqq.sqlite3")
RETRYABLE = {"timeout", "pacing"}
STOP_STATUSES = {"disconnected", "entitlement", "pacing", "unit_unverified"}


class HistoryClient(Protocol):
    volume_unit: str | None

    def qualify_qqq(self) -> ContractIdentity: ...
    def request_session(self, contract: ContractIdentity, session: Session) -> Attempt: ...


@dataclass
class BackfillSummary:
    cutoff: date
    first_bar: int | None
    last_bar: int | None
    expected: int
    received: int
    missing: int
    conflicts: int
    pending_dates: list[date]
    failed_window: date | None = None
    diagnostic: str = ""

    @property
    def ok(self) -> bool:
        return not self.pending_dates and not self.failed_window


def summarize(store: Store, target: list[Session], failed: Session | None = None,
              diagnostic: str = "") -> BackfillSummary:
    with store.reader() as db:
        lower, upper = target[0].open_utc, target[-1].close_utc
        received, first_bar, last_bar = db.execute(
            "SELECT COUNT(*), MIN(time), MAX(time) FROM bars WHERE time>=? AND time<?", (lower, upper),
        ).fetchone()
        conflicts = db.execute(
            "SELECT COUNT(DISTINCT time) FROM conflicts WHERE time>=? AND time<?", (lower, upper),
        ).fetchone()[0]
        pending_dates = [
            session.date for session in target if not store.has_completed_response(db, session)
            or len(store._times(db, session)) != len(session.minutes)
        ]
    expected = sum(len(session.minutes) for session in target)
    return BackfillSummary(target[-1].date, first_bar, last_bar, expected, received, expected - received,
                           conflicts, pending_dates, failed.date if failed else None, diagnostic)


def _save_probe(store: Store, client: HistoryClient, contract: ContractIdentity, session: Session) -> str:
    attempt = client.request_session(contract, session)
    bars = [bar for raw in attempt.raw if (bar := Bar.from_raw(raw, session))]
    previous = {b.time: b.volume for b in store.bars_between(session.open_utc, session.close_utc)}
    diagnostic = attempt.diagnostic
    status = attempt.status
    ratios = [bar.volume / previous[bar.time] for bar in bars if previous.get(bar.time, 0) > 0]
    if not bars:
        if status == "complete":
            status = "empty"
        diagnostic = diagnostic or "The first January 2020 session returned no usable bars."
    elif client.volume_unit is None:
        status = "unit_unverified"
        diagnostic = "Set --volume-unit shares or lots to match the TWS API volume setting."
    elif previous and (len(ratios) < 20 or not 0.25 <= median(ratios) <= 4):
        status = "unit_unverified"
        diagnostic = "TWS volume scale differs from the cached January probe or cannot be verified."
    elif status != "complete":
        diagnostic = diagnostic or f"January probe ended with status {status}."
    store.record_attempt(session, Attempt(status, attempt.raw, diagnostic, attempt.request))
    return diagnostic


def history_duration(first: Session, last: Session) -> str:
    span = last.close_utc - first.open_utc
    days = (span + 86399) // 86400
    return "1 M" if days > 7 else f"{days} D"


def request_windows(target: list[Session]):
    for _, group in groupby(target, key=lambda session: (session.date.year, session.date.month)):
        yield list(group)


def save_window(store: Store, pending: list[Session], attempt: Attempt) -> None:
    groups = {session: [] for session in pending}
    sessions_by_minute = {minute: session for session in pending for minute in session.minutes}
    for raw in attempt.raw:
        try:
            minute = int(raw[0])
        except ValueError:
            minute = None
        groups[sessions_by_minute.get(minute, pending[-1])].append(raw)
    for session, rows in groups.items():
        store.record_attempt(session, Attempt(attempt.status, rows, attempt.diagnostic, attempt.request))


def run_backfill(store: Store, client: HistoryClient, now: datetime, probe_only: bool,
                 *, wait=sleep) -> BackfillSummary:
    target = completed_sessions(now)
    if not target:
        raise ValueError("No completed QQQ regular sessions in the requested range.")
    first = target[0]
    if client.volume_unit is not None:
        store.require_volume_unit(client.volume_unit)
    if not probe_only:
        store.set_target(target[-1].date)
    if not probe_only and client.volume_unit is not None and not any(map(store.pending, target)):
        return summarize(store, target)
    contract = client.qualify_qqq()
    diagnostic = _save_probe(store, client, contract, first)
    if probe_only or diagnostic:
        return summarize(store, [first] if probe_only else target, first if diagnostic else None, diagnostic)

    for window in request_windows(target):
        pending = [session for session in window if store.pending(session)]
        if len(pending) > 1 and hasattr(client, "request_bars"):
            duration = history_duration(pending[0], pending[-1])
            attempt = client.request_bars(contract, pending[-1], duration)
            save_window(store, pending, attempt)
            print(f"{pending[0].date} to {pending[-1].date}  {attempt.status}  {len(attempt.raw)} provider rows"
                  f"{'  ' + attempt.diagnostic if attempt.diagnostic else ''}", flush=True)
            if attempt.status in STOP_STATUSES:
                return summarize(store, target, pending[0], attempt.diagnostic)
        for session in pending:
            if not store.pending(session):
                continue
            for attempt_number in range(3):
                attempt = client.request_session(contract, session)
                store.record_attempt(session, attempt)
                if attempt.status not in RETRYABLE or attempt_number == 2:
                    break
                wait(20 * (attempt_number + 1))
            if attempt.status in STOP_STATUSES:
                return summarize(store, target, session, attempt.diagnostic)
            print(f"{session.date}  {attempt.status}  {len(attempt.raw)} provider rows"
                  f"{'  ' + attempt.diagnostic if attempt.diagnostic else ''}", flush=True)
    return summarize(store, target)


def print_summary(summary: BackfillSummary) -> None:
    def local(timestamp: int | None) -> str:
        if timestamp is None:
            return "none"
        return datetime.fromtimestamp(timestamp, NEW_YORK).isoformat(timespec="minutes")

    print(f"Target through {summary.cutoff}; received {summary.received:,}/{summary.expected:,}; "
          f"missing {summary.missing:,}; conflicting minutes {summary.conflicts:,}.")
    print(f"New York coverage: {local(summary.first_bar)} to {local(summary.last_bar)}.")
    if summary.pending_dates:
        print("Incomplete sessions: " + ", ".join(map(str, summary.pending_dates)))
    if summary.failed_window:
        print(f"{summary.failed_window}: {summary.diagnostic}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download QQQ one-minute regular-session history from TWS.")
    parser.add_argument("--probe", action="store_true", help="Verify the January 2, 2020 session only.")
    parser.add_argument("--port", type=int, default=7497)
    parser.add_argument("--client-id", type=int, default=15907)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--volume-unit", choices=("shares", "lots"))
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    try:
        with TwsHistory(args.port, args.client_id, 25, volume_unit=args.volume_unit) as client:
            summary = run_backfill(Store(args.db), client, now, args.probe)
    except (ConnectionError, ImportError, OSError, ValueError) as exc:
        target = completed_sessions(now)
        cutoff = target[0 if args.probe else -1].date if target else START_DATE
        print(f"QQQ {START_DATE} through {cutoff}: {exc}", file=sys.stderr)
        return 1
    print_summary(summary)
    return 0 if summary.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
