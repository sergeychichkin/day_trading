from datetime import date, datetime, timezone

import pytest

from qqq_viewer.backfill import run_backfill
from qqq_viewer.sessions import completed_sessions
from qqq_viewer.store import Attempt, Store
from qqq_viewer.tws import ContractIdentity, historical_parameters

NOW = datetime(2020, 1, 4, tzinfo=timezone.utc)


class FakeHistory:
    volume_unit = "shares"

    def __init__(self):
        self.requested = []
        self.fail = None

    def qualify_qqq(self):
        return ContractIdentity(320227571, "NASDAQ")

    def request_session(self, contract, session):
        self.requested.append(session.date)
        if session.date == self.fail:
            return Attempt("disconnected", diagnostic="Lost connection")
        return Attempt("complete", [(str(t), "210", "211", "209", "210", "100") for t in session.minutes])


def test_historical_parameters_are_rth_trades():
    first = completed_sessions(NOW)[0]
    assert historical_parameters(first) == ("20200102-21:00:00", "1 D", "1 min", "TRADES", 1, 2, False, [])


def test_resume_retries_missing_session(tmp_path):
    cache = Store(tmp_path / "data.db")
    client = FakeHistory()
    client.fail = date(2020, 1, 3)
    summary = run_backfill(cache, client, NOW, False)
    assert summary.failed_window == client.fail
    assert summary.received == 390
    client.fail = None
    client.requested.clear()
    summary = run_backfill(cache, client, NOW, False)
    assert client.requested == [date(2020, 1, 2), date(2020, 1, 3)]
    assert summary.received == 780
    assert summary.missing == 0
    assert summary.ok


def test_change_in_volume_units_is_rejected_before_requests(tmp_path):
    cache = Store(tmp_path / "data.db")
    cache.require_volume_unit("lots")
    client = FakeHistory()
    with pytest.raises(ValueError, match="disagrees"):
        run_backfill(cache, client, NOW, False)
    assert not client.requested


def test_monthly_response_is_divided_by_session_and_recovers_empty_days(tmp_path):
    class BatchedHistory(FakeHistory):
        def request_bars(self, contract, last, duration):
            self.requested.append(duration)
            monday = next(s for s in completed_sessions(now) if s.date == date(2020, 1, 6))
            return Attempt("complete", [(str(t), "210", "211", "209", "210", "100") for t in monday.minutes])

    now = datetime(2020, 1, 8, tzinfo=timezone.utc)
    client = BatchedHistory()
    summary = run_backfill(Store(tmp_path / "cache.db"), client, now, False)
    assert client.requested == [date(2020, 1, 2), "5 D", date(2020, 1, 3), date(2020, 1, 7)]
    assert summary.received == 1560
    assert summary.ok


def test_refuses_live_volume_scale_change_with_same_cli_label(tmp_path):
    class ChangingHistory(FakeHistory):
        scale = 100

        def request_session(self, contract, session):
            attempt = super().request_session(contract, session)
            attempt.raw = [(*row[:-1], str(self.scale)) for row in attempt.raw]
            return attempt

    cache = Store(tmp_path / "data.db")
    client = ChangingHistory()
    probe = run_backfill(cache, client, NOW, True)
    assert probe.ok
    client.scale = 1
    client.requested.clear()
    result = run_backfill(cache, client, NOW, False)
    assert client.requested == [date(2020, 1, 2)]
    assert result.failed_window == date(2020, 1, 2)
    assert "volume scale" in result.diagnostic.lower()
    assert result.received == 390


def test_provider_uses_calendar_month_for_longer_windows():
    from qqq_viewer.backfill import history_duration

    first = completed_sessions(NOW)[0]
    assert history_duration(first, first) == "1 D"
    last = completed_sessions(datetime(2020, 2, 1, tzinfo=timezone.utc))[-1]
    assert history_duration(first, last) == "1 M"
