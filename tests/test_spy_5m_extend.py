import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from qqq_viewer import backtest_spy_5m, spy_5m_contiguous, spy_5m_store, spy_5m_view
from qqq_viewer.spy_5m_snapshot import read_snapshot
from qqq_viewer.spy_5m_store import FiveMinuteStore, expected_times, sessions
from qqq_viewer.tws import ContractIdentity
from tests.test_spy_5m_sierra import _write_scid
from tests.test_spy_5m_store import bars_for


def _seed_published_tws(
    path: Path, start: date, end: date, *, diagnostic: str = "original-tws",
    volume_unit: str = "shares", requested_through: str | None = None,
) -> FiveMinuteStore:
    store = FiveMinuteStore(path)
    store.configure(756733, volume_unit)
    win = sessions(start, end)
    for session in win:
        store.record(session, "complete", bars_for(spy_5m_store, session), diagnostic)
    store.set_target(end)
    with store._connection(write=True) as db:
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('verified_contiguous_start', ?)", (start.isoformat(),))
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('verified_contiguous_end', ?)", (end.isoformat(),))
        if requested_through is not None:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('requested_through', ?)", (requested_through,))
    return store


def _write_1m_scid_for_sessions(
    path: Path, session_list, *, skip_first_bar_on: set[date] = frozenset(),
    skip_minute_on: set[date] = frozenset(), price: float = 150.0,
) -> None:
    records = []
    for session in session_list:
        first_slot = next(iter(expected_times(session)))
        for minute_ts in range(session.open_utc, session.close_utc, 60):
            if session.date in skip_first_bar_on and minute_ts < first_slot + 300:
                continue
            if session.date in skip_minute_on and minute_ts == session.open_utc + 60:
                continue
            dt = datetime.fromtimestamp(minute_ts, timezone.utc)
            records.append((dt, price, price + 1.0, price - 1.0, price + 0.5, 0, 200, 0, 0))
    _write_scid(path, records)


def _write_daily_cache(path: Path, start: date, end: date, price_by_date: dict[str, float] | None = None) -> Path:
    price_by_date = price_by_date or {}
    rows = []
    for session in sessions(start, end):
        d = session.date.isoformat()
        p = price_by_date.get(d, 150.0)
        rows.append({
            "time": d, "open": p, "high": p + 2.0, "low": p - 2.0, "close": p + 0.5,
            "volume": 1_000_000, "upper": p + 1.5, "lower": p - 1.5,
        })
    path.write_text(json.dumps({"schema": "yahoo-daily-v1", "ticker": "SPY", "source": "Yahoo Finance", "bars": rows}),
                    encoding="utf-8")
    return path


class _FakeTws:
    def __init__(
        self, *, con_id: int = 756733, empty_dates: set[str] = frozenset(),
        partial_dates: set[str] = frozenset(), partial_batch_dates: set[str] = frozenset(),
        timeout_once_dates: set[str] = frozenset(), stop_status: str | None = None, wap: float = 200.5,
    ):
        self.con_id = con_id
        self.empty_dates = set(empty_dates)
        self.partial_dates = set(partial_dates)
        self.partial_batch_dates = set(partial_batch_dates)
        self.timeout_once_dates = set(timeout_once_dates)
        self.stop_status = stop_status
        self.wap = wap
        self.requests: list[tuple[tuple[str, ...], str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def qualify_spy(self):
        return ContractIdentity(self.con_id, "ARCA")

    def request(self, window, duration):
        dates = tuple(s.date.isoformat() for s in window)
        self.requests.append((dates, duration))
        if self.stop_status is not None:
            return self.stop_status, [], f"forced {self.stop_status}"
        if len(window) == 1 and dates[0] in self.timeout_once_dates:
            self.timeout_once_dates.remove(dates[0])
            return "timeout", [], "transient timeout"
        bars = []
        for session in window:
            key = session.date.isoformat()
            if key in self.empty_dates:
                continue
            session_bars = [replace(bar, wap=self.wap) for bar in bars_for(spy_5m_store, session)]
            if key in self.partial_dates or (len(window) > 1 and key in self.partial_batch_dates):
                session_bars = session_bars[:10]
            bars.extend(session_bars)
        return ("complete", bars, "") if bars else ("empty", [], "no data")


def test_extends_published_tws_cache_with_sierra_and_tws_patch(tmp_path):
    from qqq_viewer.spy_5m_extend import extend_spy_5m

    source = tmp_path / "spy_5m.sqlite3"
    out = tmp_path / "out.sqlite3"
    stage = tmp_path / "stage.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    scid = tmp_path / "SPY.scid"
    daily = _write_daily_cache(tmp_path / "daily.json", date(2025, 11, 21), date(2025, 11, 28),
                               {"2025-11-26": 100.0, "2025-11-28": 100.0})

    _seed_published_tws(source, date(2025, 11, 26), date(2025, 11, 28), requested_through="2025-12-01")
    pre = sessions(date(2025, 11, 21), date(2025, 11, 25))
    # 2025-11-21 full, 2025-11-24 missing 1m at 09:31 (so 1m completeness fails!), 2025-11-25 full
    _write_1m_scid_for_sessions(scid, pre, skip_minute_on={date(2025, 11, 24)})
    tws = _FakeTws(wap=200.5)

    report = extend_spy_5m(
        scid, source, out, stage,
        floor=date(2025, 11, 21),
        daily_cache=daily,
        history_factory=lambda: tws,
        backup_path=backup,
        promote=True,
    )

    assert (report["start"], report["through"], report["sessions"], report["blocked"]) == (
        "2025-11-21", "2025-11-28", 5, None,
    )
    assert (report["sierra_sessions"], report["tws_patch_sessions"], report["preserved_tws_sessions"]) == (2, 1, 2)
    assert tws.requests == [(("2025-11-24",), "1 D")]

    verified = spy_5m_contiguous.verify_published(FiveMinuteStore(source, read_only=True))
    assert (verified["start"], verified["through"], verified["sessions"]) == ("2025-11-21", "2025-11-28", 5)

    backup_verified = spy_5m_contiguous.verify_published(FiveMinuteStore(backup, read_only=True))
    assert (backup_verified["start"], backup_verified["through"], backup_verified["sessions"]) == (
        "2025-11-26", "2025-11-28", 2,
    )

    with FiveMinuteStore(source, read_only=True)._connection() as db:
        diag = dict(db.execute("SELECT date, diagnostic FROM attempts").fetchall())
        assert diag["2025-11-21"] == "sierra-scid-1m"
        assert diag["2025-11-24"] == "tws-1d-patch"
        assert diag["2025-11-25"] == "sierra-scid-1m"
        assert diag["2025-11-26"] == "original-tws"
        assert db.execute("SELECT value FROM metadata WHERE key='requested_through'").fetchone()[0] == "2025-12-01"
        assert db.execute("SELECT wap FROM bars WHERE date='2025-11-24' LIMIT 1").fetchone()[0] == 200.5
        assert db.execute("SELECT wap FROM bars WHERE date='2025-11-26' LIMIT 1").fetchone()[0] == 100.5

    for suffix in ("-wal", "-shm", "-journal"):
        assert not Path(str(source) + suffix).exists()
        assert not Path(str(stage) + suffix).exists()


def test_partial_tws_does_not_poison_staging_and_resume_reports_actual_counts(tmp_path):
    from qqq_viewer.spy_5m_extend import extend_spy_5m

    source = tmp_path / "spy_5m.sqlite3"
    stage = tmp_path / "stage.sqlite3"
    scid = tmp_path / "SPY.scid"
    daily = _write_daily_cache(tmp_path / "daily.json", date(2025, 11, 21), date(2025, 11, 28))
    _seed_published_tws(source, date(2025, 11, 26), date(2025, 11, 28))
    pre = sessions(date(2025, 11, 21), date(2025, 11, 25))
    _write_1m_scid_for_sessions(scid, pre, skip_first_bar_on={date(2025, 11, 24)})

    r1 = extend_spy_5m(
        scid, source, tmp_path / "out1.sqlite3", stage,
        floor=date(2025, 11, 21), daily_cache=daily,
        history_factory=lambda: _FakeTws(partial_dates={"2025-11-24"}),
    )
    assert (r1["start"], r1["blocked"]) == ("2025-11-25", "2025-11-24")

    # Retry with complete TWS response (and different wap=300.5): must NOT hit conflicts in staging!
    r2 = extend_spy_5m(
        scid, source, tmp_path / "out2.sqlite3", stage,
        floor=date(2025, 11, 21), daily_cache=daily,
        history_factory=lambda: _FakeTws(wap=300.5),
    )
    assert (r2["start"], r2["sessions"], r2["blocked"]) == ("2025-11-21", 5, None)
    assert (r2["sierra_sessions"], r2["tws_patch_sessions"], r2["preserved_tws_sessions"]) == (2, 1, 2)


def test_batches_months_and_retries_transient_tws_timeout(tmp_path):
    from qqq_viewer.spy_5m_extend import extend_spy_5m

    source = tmp_path / "spy_5m.sqlite3"
    scid = tmp_path / "SPY.scid"
    daily = _write_daily_cache(tmp_path / "daily.json", date(2025, 11, 21), date(2025, 11, 28))
    _seed_published_tws(source, date(2025, 11, 26), date(2025, 11, 28))
    _write_1m_scid_for_sessions(scid, sessions(date(2025, 11, 21), date(2025, 11, 21)))
    tws = _FakeTws(partial_batch_dates={"2025-11-24"}, timeout_once_dates={"2025-11-24"})
    r = extend_spy_5m(
        scid, source, tmp_path / "out.sqlite3", tmp_path / "stage.sqlite3",
        floor=date(2025, 11, 21), daily_cache=daily,
        history_factory=lambda: tws, retry_delay=0.0,
    )
    assert (r["start"], r["sessions"], r["blocked"]) == ("2025-11-21", 5, None)
    assert any(dur == "1 M" for _, dur in tws.requests)
    assert sum(1 for dates, dur in tws.requests if dur == "1 D" and dates == ("2025-11-24",)) == 2


def test_guards_inputs_units_price_basis_scid_change_and_promotion(tmp_path):
    from qqq_viewer.spy_5m_extend import extend_spy_5m

    source = tmp_path / "spy_5m.sqlite3"
    scid = tmp_path / "SPY.scid"
    pre = sessions(date(2025, 11, 21), date(2025, 11, 25))
    _write_1m_scid_for_sessions(scid, pre, skip_first_bar_on={date(2025, 11, 24)})
    daily = _write_daily_cache(tmp_path / "daily.json", date(2025, 11, 21), date(2025, 11, 28))

    # Rejects volume_unit="lots"
    lots_db = tmp_path / "lots.sqlite3"
    _seed_published_tws(lots_db, date(2025, 11, 26), date(2025, 11, 28), volume_unit="lots")
    with pytest.raises(ValueError, match="shares"):
        extend_spy_5m(scid, lots_db, tmp_path / "out_lots.sqlite3", tmp_path / "st_lots.sqlite3",
                      floor=date(2025, 11, 21), daily_cache=daily, history_factory=lambda: _FakeTws())

    _seed_published_tws(source, date(2025, 11, 26), date(2025, 11, 28))

    # Rejects >5% daily price basis mismatch
    bad_daily = _write_daily_cache(tmp_path / "bad_daily.json", date(2025, 11, 21), date(2025, 11, 28),
                                   {"2025-11-25": 300.0})
    with pytest.raises(ValueError, match="price basis"):
        extend_spy_5m(scid, source, tmp_path / "out_bad_d.sqlite3", tmp_path / "st_bad_d.sqlite3",
                      floor=date(2025, 11, 21), daily_cache=bad_daily, history_factory=lambda: _FakeTws())

    # Rejects --backup without --promote, and --promote without --backup (unless allow_no_backup)
    with pytest.raises(ValueError, match="promote"):
        extend_spy_5m(scid, source, tmp_path / "o1.sqlite3", tmp_path / "s1.sqlite3",
                      floor=date(2025, 11, 21), daily_cache=daily, backup_path=tmp_path / "bak.sqlite3", promote=False)
    with pytest.raises(ValueError, match="backup"):
        extend_spy_5m(scid, source, tmp_path / "o2.sqlite3", tmp_path / "s2.sqlite3",
                      floor=date(2025, 11, 21), daily_cache=daily, promote=True)

    # Rejects unfillable gap on promote=True without creating backup or touching source
    unused_backup = tmp_path / "unused_bak.sqlite3"
    stage_reuse = tmp_path / "st_reuse.sqlite3"
    with pytest.raises(ValueError, match="Blocked at 2025-11-24"):
        extend_spy_5m(scid, source, tmp_path / "o3.sqlite3", stage_reuse,
                      floor=date(2025, 11, 21), daily_cache=daily,
                      history_factory=lambda: _FakeTws(empty_dates={"2025-11-24"}),
                      backup_path=unused_backup, promote=True)
    assert not unused_backup.exists()
    assert spy_5m_contiguous.verify_published(FiveMinuteStore(source, read_only=True))["start"] == "2025-11-26"

    # Rejects changing SCID file against existing staging_db unless reset_staging=True
    scid2 = tmp_path / "SPY2.scid"
    _write_1m_scid_for_sessions(scid2, pre, price=155.0)
    with pytest.raises(ValueError, match="reset-staging"):
        extend_spy_5m(scid2, source, tmp_path / "o4.sqlite3", stage_reuse,
                      floor=date(2025, 11, 21), daily_cache=daily, history_factory=lambda: _FakeTws())


def test_cli_main_and_downstream_consumers(tmp_path, monkeypatch):
    from qqq_viewer import spy_5m_extend

    source = tmp_path / "spy_5m.sqlite3"
    scid = tmp_path / "SPY.scid"
    out = tmp_path / "out.sqlite3"
    stage = tmp_path / "stage.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    daily = _write_daily_cache(tmp_path / "daily.json", date(2025, 11, 21), date(2025, 12, 1),
                               {"2025-11-26": 100.0, "2025-11-28": 100.0, "2025-12-01": 100.0})
    _seed_published_tws(source, date(2025, 11, 26), date(2025, 11, 28))
    _write_1m_scid_for_sessions(scid, sessions(date(2025, 11, 21), date(2025, 11, 25)))

    monkeypatch.setattr(spy_5m_extend, "SpyHistory", lambda *a, **kw: _FakeTws())
    monkeypatch.setattr("sys.argv", [
        "spy_5m_extend", "--scid", str(scid), "--db", str(source), "--out-db", str(out),
        "--staging-db", str(stage), "--daily-cache", str(daily), "--backup", str(backup), "--promote",
    ])
    assert spy_5m_extend.main() == 0

    # Downstream 1: spy_5m_view
    payload = spy_5m_view.month_payload(FiveMinuteStore(source, read_only=True), daily, "2025-11")
    assert payload["published_start"] == "2025-11-21"
    assert len(payload["sessions"]) == 5

    # Downstream 2: backtest_spy_5m
    cfg_path = tmp_path / "SPY.toml"
    cfg_path.write_text(f'ticker="SPY"\nintraday_cache="{source.name}"\ndaily_cache="{daily.name}"\n', encoding="utf-8")
    bt = backtest_spy_5m.run(backtest_spy_5m.load_settings(cfg_path))
    assert (bt["coverage"]["start"], bt["coverage"]["end"]) == ("2025-11-21", "2025-11-28")

    # Downstream 3: spy_5m_contiguous tail refresh onto 2025-12-01
    tws_tail = _FakeTws()
    tail_stage = FiveMinuteStore(tmp_path / "tail_stage.sqlite3")
    res = spy_5m_contiguous.acquire(
        FiveMinuteStore(source), tail_stage, tws_tail,
        datetime(2025, 12, 2, tzinfo=timezone.utc), through=date(2025, 12, 1), volume_unit="shares",
    )
    assert (res["start"], res["through"], res["sessions"]) == ("2025-11-21", "2025-12-01", 6)


def test_read_snapshot_handles_non_empty_wal_without_shm(tmp_path):
    import sqlite3

    store = FiveMinuteStore(tmp_path / "wal_source.sqlite3")
    store.configure(756733, "shares")
    sess, = sessions(date(2025, 11, 25), date(2025, 11, 25))
    store.record(sess, "complete", bars_for(spy_5m_store, sess))
    # Keep a read lock open while writing a second session so -wal stays non-empty, then remove -shm on a copy.
    sess2, = sessions(date(2025, 11, 26), date(2025, 11, 26))
    wal = Path(str(store.path) + "-wal")
    shm = Path(str(store.path) + "-shm")
    holder = sqlite3.connect(store.path)
    holder.execute("BEGIN")
    holder.execute("SELECT COUNT(*) FROM bars").fetchone()
    store.record(sess2, "complete", bars_for(spy_5m_store, sess2))
    assert wal.exists() and wal.stat().st_size > 0
    # Copy DB + WAL without SHM while holder is still open to simulate a live WAL snapshot without -shm.
    copied = tmp_path / "copied_wal.sqlite3"
    copied.write_bytes(store.path.read_bytes())
    Path(str(copied) + "-wal").write_bytes(wal.read_bytes())
    holder.close()
    assert not Path(str(copied) + "-shm").exists()
    with read_snapshot(copied) as db:
        assert db.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 156
