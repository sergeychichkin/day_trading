from datetime import date, datetime, timezone

from equity_backtest.history_5m import FiveMinuteBar, expected_times, sessions


def test_cutoff_and_exact_roster():
    from equity_backtest.acquire_history import TICKERS, cutoff_at

    assert len(set(TICKERS)) == 23 and "PFF" in TICKERS and "PDD" not in TICKERS
    assert cutoff_at(datetime(2025, 11, 28, 17, tzinfo=timezone.utc)) == date(2025, 11, 26)
    assert cutoff_at(datetime(2025, 11, 29, tzinfo=timezone.utc)) == date(2025, 11, 28)


def test_archive_recovers_saved_response_without_provider_repeat(tmp_path):
    from equity_backtest.acquisition_archive import Archive

    first = Archive(tmp_path)
    first.save("QQQ", "tws", "2025-11", {"status": "empty", "rows": []})
    later = Archive(tmp_path)
    assert later.responses("QQQ", "tws", "2025-11") == [{"status": "empty", "rows": []}]


def test_five_minute_audit_retries_and_records_later_blocks(tmp_path):
    from equity_backtest.history_audit import SessionEvidence, verified_blocks

    window = sessions(date(2025, 11, 26), date(2025, 12, 2))
    evidence = {session.date: SessionEvidence(session) for session in window}
    for session in window:
        bars = [FiveMinuteBar(t, 100, 101, 99, 100, 100, 100) for t in expected_times(session)]
        item = evidence[session.date]
        if session.date == date(2025, 11, 28):
            item.add("timeout", bars, "monthly")
            assert len(bars) == 42 and not item.verified
        else:
            item.add("complete", bars, "monthly")
            assert item.verified
    assert verified_blocks(window, {day for day, item in evidence.items() if item.verified}) == [
        (date(2025, 11, 26), date(2025, 11, 26)), (date(2025, 12, 1), date(2025, 12, 2)),
    ]
    half_day = evidence[date(2025, 11, 28)]
    half_day.add("complete", list(half_day.bars.values()), "daily retry")
    assert half_day.verified
    half_day.add("complete", [FiveMinuteBar(half_day.session.open_utc, 100, 102, 99, 100, 100, 100)], "conflict")
    assert not half_day.verified and half_day.conflicts


def test_daily_rejects_duplicates_and_preserves_missing_dates():
    from equity_backtest.daily_history import daily_snapshot

    dates = [s.date for s in sessions(date(2025, 11, 25), date(2025, 11, 28))]
    raw = {"status": "complete", "csv": "Date,Open,High,Low,Close,Volume,Dividends\n"
           "2025-11-25,100,101,99,100,1000,0.5\n2025-11-26,100,101,99,100,1000,0\n"
           "2025-11-26,100,101,99,100,1000,0\n2025-11-28,100,90,99,100,1000,0\n"}
    snapshot, flags = daily_snapshot("XLK", dates, raw)
    assert snapshot["ticker"] == "XLK" and len(snapshot["bars"]) == 1
    assert snapshot["missing_sessions"] == ["2025-11-26", "2025-11-28"]
    assert [flag[1] for flag in flags] == ["Dividends", "daily_duplicate", "daily_duplicate", "invalid_daily_bar"]


def test_audit_publishes_latest_block_and_enumerates_exact_half_day_hole(tmp_path):
    from contextlib import ExitStack

    from equity_backtest.history_audit import SessionEvidence, audit_symbol, publication_bundle, setup_writers

    window = sessions(date(2025, 11, 26), date(2025, 12, 2))
    evidence = {s.date: SessionEvidence(s) for s in window}
    for session in (window[0], window[2]):
        evidence[session.date].add("complete", [FiveMinuteBar(t, 100, 101, 99, 100, 100, 100)
                                                for t in expected_times(session)], "raw#1")
    evidence[window[1].date].add("empty", [], "day#1")
    daily = {"ticker": "XLK", "schema": "yahoo-daily-v1", "source": "Yahoo Finance",
             "bars": [{"time": str(s.date), "open": 100, "high": 101, "low": 99, "close": 100,
                       "lower": 99, "upper": 101} for s in window]}

    def release(block):
        return publication_bundle(tmp_path, "XLK", {"con_id": 101002}, "shares", block, daily, evidence)

    with ExitStack() as stack:
        summary, receipt = audit_symbol("XLK", window, None, daily, [], "complete", evidence, [],
                                        setup_writers(stack, tmp_path / "audit"), release)
    assert receipt["start"] == receipt["end"] == "2025-12-01"
    assert summary["verified_blocks"] == 2 and summary["tail_sessions"] == 1
    missing = (tmp_path / "audit" / "missing_5m_bars.csv").read_text()
    assert missing.count("XLK,2025-11-28,") == 42
    assert "2025-11-28T14:30:00+00:00,2025-11-28T09:30:00-05:00,empty" in missing


def test_completed_window_omission_is_not_an_empty_response(tmp_path):
    from contextlib import ExitStack

    from equity_backtest.history_audit import SessionEvidence, audit_symbol, setup_writers

    window = sessions(date(2025, 11, 28), date(2025, 11, 28))
    day = window[0].date
    evidence = {day: SessionEvidence(window[0])}
    evidence[day].add("complete", [], "month#1")
    with ExitStack() as stack:
        summary, _ = audit_symbol("XLK", window, None, {"bars": []}, [], "empty", evidence, [],
                                  setup_writers(stack, tmp_path), None)
    assert summary["session_statuses"] == {"omitted_or_invalid": 1}


def test_truncated_completed_retry_has_a_second_attempt_and_resumes(tmp_path):
    from equity_backtest.acquire_history import pull
    from equity_backtest.acquisition_archive import Archive

    session, = sessions(date(2025, 11, 28), date(2025, 11, 28))
    rows = [[timestamp, str(timestamp), "100", "101", "99", "100", "100", "100"]
            for timestamp in expected_times(session)]

    class Transport:
        attempts = 0

        def wait(self, seconds):
            assert seconds == 3

        def fetch(self, identity, window, duration):
            assert identity["ticker"] == "QQQ" and window == [session] and duration == "1 D"
            self.attempts += 1
            return {"start": str(session.date), "end": str(session.date), "status": "complete",
                    "rows": rows[:-1] if self.attempts == 1 else rows}

    client = Transport()
    key = str(session.date)
    for _ in range(2):
        response = pull(Archive(tmp_path), client, {"ticker": "QQQ"}, [session], "1 D", key)
        assert [len(record["rows"]) for record in response] == [41, 42]
    assert client.attempts == 2
