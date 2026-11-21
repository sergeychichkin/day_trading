from datetime import date, datetime, timezone

from qqq_viewer.sessions import completed_sessions, sessions


def test_requested_range_starts_after_new_year_holiday():
    assert sessions(date(2020, 1, 1), date(2020, 1, 3))[0].date == date(2020, 1, 2)


def test_calendar_preserves_early_close_and_dst():
    early = sessions(date(2020, 11, 27), date(2020, 11, 27))[0]
    assert len(early.minutes) == 210
    before, after = sessions(date(2020, 3, 6), date(2020, 3, 9))
    assert [datetime.fromtimestamp(s.open_utc, timezone.utc).hour for s in (before, after)] == [14, 13]


def test_cutoff_excludes_in_progress_session():
    now = datetime(2020, 1, 3, 20, tzinfo=timezone.utc)
    assert [s.date for s in completed_sessions(now)] == [date(2020, 1, 2)]
