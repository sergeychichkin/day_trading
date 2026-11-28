from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from qqq_viewer import spy_5m_contiguous, spy_5m_store
from qqq_viewer.spy_5m_store import FiveMinuteBar, FiveMinuteStore, expected_times, sessions
from qqq_viewer.tws import ContractIdentity
from tests.test_spy_5m_sierra import _write_scid
from tests.test_spy_5m_store import bars_for


def _seed_published_tws(path: Path, start: date, end: date, *, diagnostic: str = "original-tws") -> FiveMinuteStore:
    store = FiveMinuteStore(path)
    store.configure(756733, "shares")
    win = sessions(start, end)
    for session in win:
        store.record(session, "complete", bars_for(spy_5m_store, session), diagnostic)
    store.set_target(end)
    with store._connection(write=True) as db:
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('verified_contiguous_start', ?)", (start.isoformat(),))
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('verified_contiguous_end', ?)", (end.isoformat(),))
    return store


def _write_1m_scid_for_sessions(path: Path, session_list, *, skip_first_bar_on: set[date] = frozenset()) -> None:
    records = []
    for session in session_list:
        slots = list(expected_times(session))
        if session.date in skip_first_bar_on:
            slots = slots[1:]
        for slot in slots:
            dt = datetime.fromtimestamp(slot, timezone.utc)
            records.append((dt, 150.0, 151.0, 149.0, 150.5, 0, 1000, 0, 0))
    _write_scid(path, records)


class _FakeTws:
    def __init__(self, *, con_id: int = 756733, empty_dates: set[str] = frozenset(), wap: float = 200.5):
        self.con_id = con_id
        self.empty_dates = empty_dates
        self.wap = wap
        self.requested: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def qualify_spy(self):
        return ContractIdentity(self.con_id, "ARCA")

    def request(self, window, duration):
        session, = window
        key = session.date.isoformat()
        self.requested.append(key)
        if key in self.empty_dates:
            return "empty", [], "no data"
        bars = [replace(bar, wap=self.wap) for bar in bars_for(spy_5m_store, session)]
        return "complete", bars, ""


def test_extends_published_tws_cache_with_sierra_and_tws_patch(tmp_path):
    from qqq_viewer.spy_5m_extend import extend_spy_5m

    source = tmp_path / "spy_5m.sqlite3"
    out = tmp_path / "out.sqlite3"
    stage = tmp_path / "stage.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    scid = tmp_path / "SPY.scid"

    _seed_published_tws(source, date(2025, 11, 26), date(2025, 11, 28))
    pre = sessions(date(2025, 11, 21), date(2025, 11, 25))
    _write_1m_scid_for_sessions(scid, pre, skip_first_bar_on={date(2025, 11, 24)})
    tws = _FakeTws(wap=200.5)

    report = extend_spy_5m(
        scid, source, out, stage,
        floor=date(2025, 11, 21),
        history_factory=lambda: tws,
        backup_path=backup,
        promote=True,
    )

    assert (report["start"], report["through"], report["sessions"], report["blocked"]) == (
        "2025-11-21", "2025-11-28", 5, None,
    )
    assert (report["sierra_sessions"], report["tws_patch_sessions"], report["preserved_tws_sessions"]) == (2, 1, 2)
    assert tws.requested == ["2025-11-24"]

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
        assert db.execute("SELECT wap FROM bars WHERE date='2025-11-24' LIMIT 1").fetchone()[0] == 200.5
        assert db.execute("SELECT wap FROM bars WHERE date='2025-11-26' LIMIT 1").fetchone()[0] == 100.5

    for suffix in ("-wal", "-shm", "-journal"):
        assert not Path(str(source) + suffix).exists()


def test_stops_at_unfillable_gap_and_guards_contract_and_backup(tmp_path):
    from qqq_viewer.spy_5m_extend import extend_spy_5m

    source = tmp_path / "spy_5m.sqlite3"
    _seed_published_tws(source, date(2025, 11, 26), date(2025, 11, 28))
    scid = tmp_path / "SPY.scid"
    pre = sessions(date(2025, 11, 21), date(2025, 11, 25))
    _write_1m_scid_for_sessions(scid, pre, skip_first_bar_on={date(2025, 11, 24)})

    report = extend_spy_5m(
        scid, source, tmp_path / "out_gap.sqlite3", tmp_path / "stage_gap.sqlite3",
        floor=date(2025, 11, 21),
        history_factory=lambda: _FakeTws(empty_dates={"2025-11-24"}),
    )
    assert (report["start"], report["through"], report["sessions"], report["blocked"]) == (
        "2025-11-25", "2025-11-28", 3, "2025-11-24",
    )

    with pytest.raises(ValueError, match="contract"):
        extend_spy_5m(
            scid, source, tmp_path / "out_bad_con.sqlite3", tmp_path / "stage_bad_con.sqlite3",
            floor=date(2025, 11, 21),
            history_factory=lambda: _FakeTws(con_id=999999),
        )

    existing_backup = tmp_path / "existing_backup.sqlite3"
    existing_backup.write_bytes(b"keep-me")
    with pytest.raises(ValueError, match="backup"):
        extend_spy_5m(
            scid, source, tmp_path / "out_bak.sqlite3", tmp_path / "stage_bak.sqlite3",
            floor=date(2025, 11, 21),
            history_factory=lambda: _FakeTws(),
            backup_path=existing_backup,
            promote=True,
        )
    assert existing_backup.read_bytes() == b"keep-me"
    assert spy_5m_contiguous.verify_published(FiveMinuteStore(source, read_only=True))["start"] == "2025-11-26"
