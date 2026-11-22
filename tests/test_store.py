from datetime import date

from qqq_viewer.sessions import Session
from qqq_viewer.store import Attempt, Bar, Store

SESSION = Session(date(2020, 1, 2), 1577975400, 1577975520)


def bar(minute, volume):
    return Bar(SESSION.open_utc + minute * 60, 210.0, 211.0, 209.0, 210.5, volume)


def attempt(status, *bars):
    return Attempt(status=status, raw=[b.to_raw() for b in bars])


def test_conflicting_retries_preserve_first_and_raw_values(tmp_path):
    store = Store(tmp_path / "cache.db")
    store.record_attempt(SESSION, attempt("complete", bar(0, 100)))
    store.record_attempt(SESSION, attempt("complete", bar(0, 101)))
    assert store.bars_between(SESSION.open_utc, SESSION.close_utc) == [bar(0, 100)]
    with store.reader() as db:
        assert db.execute("SELECT volume FROM raw_bars ORDER BY id").fetchall() == [("100",), ("101",)]
        assert db.execute("SELECT COUNT(*) FROM conflicts").fetchone() == (1,)


def test_partial_timeout_then_completed_response_reconciles_coverage(tmp_path):
    store = Store(tmp_path / "cache.db")
    store.record_attempt(SESSION, attempt("timeout", bar(0, 100)))
    assert store.pending(SESSION)
    assert store.missing_minutes(SESSION) == [SESSION.open_utc + 60]
    store.record_attempt(SESSION, attempt("complete", bar(1, 101), bar(2, 102)))
    assert store.missing_minutes(SESSION) == []
    assert not store.pending(SESSION)
    assert store.bars_between(SESSION.open_utc, SESSION.close_utc) == [bar(0, 100), bar(1, 101)]


def test_overlapping_row_from_later_request_flags_earlier_accepted_minute(tmp_path):
    store = Store(tmp_path / "cache.db")
    store.record_attempt(SESSION, attempt("complete", bar(0, 100)))
    later = Session(date(2020, 1, 3), SESSION.open_utc + 86400, SESSION.close_utc + 86400)
    store.record_attempt(later, attempt("complete", bar(0, 101)))
    assert store.bars_between(SESSION.open_utc, SESSION.close_utc) == [bar(0, 100)]
    with store.reader() as db:
        assert db.execute("SELECT time FROM conflicts").fetchall() == [(SESSION.open_utc,)]
