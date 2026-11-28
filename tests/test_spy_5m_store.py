from dataclasses import replace
from datetime import date, datetime, timezone
from hashlib import sha256

import pytest

from qqq_viewer.sessions import NEW_YORK
from qqq_viewer.store import Store


@pytest.fixture
def intraday():
    from qqq_viewer import spy_5m_store

    return spy_5m_store


def regular(intraday):
    return intraday.sessions(date(2000, 1, 3), date(2000, 1, 3))[0]


def bars_for(intraday, session):
    return [intraday.FiveMinuteBar(time, 100, 102, 99, 101, 10, 100.5)
            for time in intraday.expected_times(session)]


def test_expected_regular_and_early_close_bars(intraday):
    first = regular(intraday)
    early = intraday.sessions(date(2025, 11, 28), date(2025, 11, 28))[0]
    assert len(intraday.expected_times(first)) == 78
    assert len(intraday.expected_times(early)) == 42
    assert early.close_utc not in intraday.expected_times(early)
    before, after = intraday.sessions(date(2025, 3, 7), date(2025, 3, 10))
    assert [datetime.fromtimestamp(s.open_utc, NEW_YORK).strftime("%H:%M") for s in (before, after)] == [
        "09:30", "09:30",
    ]
    assert [datetime.fromtimestamp(s.open_utc, timezone.utc).hour for s in (before, after)] == [14, 13]


def test_completed_cutoff_exact_at_early_close(intraday):
    assert intraday.completed(datetime.fromisoformat("2025-11-28T17:59:59+00:00"))[-1].date == date(2025, 11, 26)
    assert intraday.completed(datetime.fromisoformat("2025-11-28T18:00:00+00:00"))[-1].date == date(2025, 11, 28)


def test_store_repairs_only_missing_wap(intraday, tmp_path):
    store = intraday.FiveMinuteStore(tmp_path / "spy.db")
    session = regular(intraday)
    store.configure(123, "shares")
    store.set_target(session.date)
    bars = bars_for(intraday, session)
    first = bars[0]
    store.record(session, "complete", [replace(first, wap=None), *bars[1:]])
    assert store.pending([session]) == [session]
    store.record(session, "complete", bars)
    assert store.pending([session]) == []
    store.record(session, "complete", [replace(first, wap=100.75)])
    row, = store.read_month("2000-01")["sessions"]
    assert row["bars"][0]["wap"] == 100.5
    assert row["conflict_times"] == [first.time]


def test_store_ignores_outside_grid_and_resumes_incomplete_sessions(intraday, tmp_path):
    store = intraday.FiveMinuteStore(tmp_path / "spy.db")
    first, second = intraday.sessions(date(2000, 1, 3), date(2000, 1, 4))
    store.configure(123, "shares")
    store.set_target(second.date)
    bars = bars_for(intraday, first)
    store.record(first, "timeout", bars + [replace(bars[0], time=first.open_utc + 60)])
    store.record(second, "complete", [replace(bars[0], time=second.close_utc)])
    assert store.pending([first, second]) == [first, second]
    assert store.coverage([first, second])["received"] == 78
    store.record(first, "complete", bars)
    assert store.pending([first, second]) == [second]
    assert store.latest_date() == first.date


def test_store_rejects_qqq_or_incompatible_metadata(intraday, tmp_path):
    qqq = tmp_path / "qqq.db"
    Store(qqq).require_volume_unit("shares")
    before = sha256(qqq.read_bytes()).digest()
    with pytest.raises(ValueError, match="five-minute cache"):
        intraday.FiveMinuteStore(qqq)
    assert sha256(qqq.read_bytes()).digest() == before
    store = intraday.FiveMinuteStore(tmp_path / "spy.db")
    store.configure(123, "shares")
    with pytest.raises(ValueError, match="volume"):
        store.configure(123, "lots")
    with pytest.raises(ValueError, match="contract"):
        store.configure(456, "shares")


def test_timeout_cannot_promote_a_session_to_verified(intraday, tmp_path):
    store = intraday.FiveMinuteStore(tmp_path / "spy.db")
    session = regular(intraday)
    store.configure(123, "shares")
    bars = bars_for(intraday, session)
    store.record(session, "complete", bars[:-1])
    store.record(session, "timeout", bars[-1:])
    assert store.pending([session]) == [session]
    store.record(session, "complete", bars)
    assert store.pending([session]) == []


def test_partial_success_cannot_certify_bars_from_a_timeout(intraday, tmp_path):
    store = intraday.FiveMinuteStore(tmp_path / "spy.db")
    session = regular(intraday)
    store.configure(123, "shares")
    bars = bars_for(intraday, session)
    store.record(session, "timeout", bars[:-1])
    store.record(session, "complete", bars[-1:])
    assert store.coverage([session])["received"] == 78
    assert store.pending([session]) == [session]
    store.record(session, "complete", [*bars[:-1], bars[-2]])
    assert store.pending([session]) == [session]
    store.record(session, "complete", bars)
    assert store.pending([session]) == []


def test_all_zero_volume_cannot_be_verified(intraday, tmp_path):
    store = intraday.FiveMinuteStore(tmp_path / "spy.db")
    session = regular(intraday)
    store.configure(123, "shares")
    rows = [replace(bar, volume=0, wap=None) for bar in bars_for(intraday, session)]
    store.record(session, "complete", rows)
    assert store.coverage([session])["received"] == 78
    assert store.pending([session]) == [session]
