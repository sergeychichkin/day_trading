from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from qqq_viewer import spy_5m_store
from qqq_viewer.spy_5m_store import FiveMinuteStore, sessions
from qqq_viewer.tws import ContractIdentity
from tests.test_spy_5m_store import bars_for


@pytest.fixture
def workflow():
    from qqq_viewer import spy_5m_contiguous

    return spy_5m_contiguous


@pytest.fixture
def stores(tmp_path):
    return FiveMinuteStore(tmp_path / "viewer.db"), FiveMinuteStore(tmp_path / "staging.db")


class FakeHistory:
    def __init__(self, gaps=()):
        self.requests = []
        self.gaps = set(gaps)

    def qualify_spy(self):
        return ContractIdentity(756733, "ARCA")

    def request(self, window, duration):
        self.requests.append((tuple(s.date.isoformat() for s in window), duration))
        bars = [bar for s in window if s.date.isoformat() not in self.gaps for bar in bars_for(spy_5m_store, s)]
        return ("complete", bars, "") if bars else ("empty", [], "no data")


def run(workflow, stores, history, through):
    day = date.fromisoformat(through)
    return workflow.acquire(*stores, history, datetime.combine(day, datetime.max.time(), timezone.utc),
                            through=day, floor=date(2025, 11, 25), volume_unit="shares")


def test_build_publishes_only_the_suffix_and_verifies_fixed_cutoff(workflow, stores):
    main, stage = stores
    client = FakeHistory(gaps=["2025-11-25"])
    report = run(workflow, stores, client, "2025-11-28")
    assert (report["start"], report["through"], report["sessions"], report["bars"]) == (
        "2025-11-26", "2025-11-28", 2, 120,
    )
    assert (("2025-11-25",), "1 D") in client.requests
    verified = workflow.verify_published(FiveMinuteStore(main.path, read_only=True), date(2025, 11, 26))
    assert (verified["sessions"], verified["bars"]) == (1, 78)
    with pytest.raises(ValueError, match="outside"):
        workflow.verify_published(main, date(2025, 12, 1))
    assert main.coverage(sessions(date(2025, 11, 25), date(2025, 11, 25)))["received"] == 0
    assert stage.pending(sessions(date(2025, 11, 25), date(2025, 11, 25)))


def test_fresh_publisher_output_is_accepted_by_offline_replay(workflow, stores, tmp_path):
    import json

    from qqq_viewer.backtest_spy_5m import load_settings, run as replay

    main, _ = stores
    run(workflow, stores, FakeHistory(), "2025-11-28")
    (tmp_path / "daily.json").write_text(json.dumps({
        "schema": "yahoo-daily-v1", "ticker": "SPY", "source": "Yahoo Finance", "bars": [],
    }), encoding="utf-8")
    config = tmp_path / "SPY.toml"
    config.write_text('ticker="SPY"\nintraday_cache="viewer.db"\ndaily_cache="daily.json"\n', encoding="utf-8")
    data = replay(load_settings(config))
    assert data["coverage"]["start"] == "2025-11-25"
    assert data["coverage"]["end"] == "2025-11-28"
    assert data["coverage"]["signal_bars"] == 198


def test_verifier_leaves_closed_wal_input_without_sidecars(workflow, stores):
    main, _ = stores
    run(workflow, stores, FakeHistory(), "2025-11-28")
    sidecars = [Path(str(main.path) + suffix) for suffix in ("-wal", "-shm", "-journal")]
    assert not any(path.exists() for path in sidecars)
    workflow.verify_published(main)
    assert not any(path.exists() for path in sidecars)


def test_verifier_rejects_uncompleted_session(workflow, stores):
    main, _ = stores
    run(workflow, stores, FakeHistory(), "2025-11-28")
    with main._connection(write=True) as db:
        db.execute("UPDATE sessions SET completed=0 WHERE date='2025-11-26'")
    with pytest.raises(ValueError, match="no verified full"):
        workflow.verify_published(main)


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_publisher_rejects_sidecar_as_staging_before_creating_files(workflow, tmp_path, monkeypatch, suffix):
    database = tmp_path / "viewer.db"
    staging = Path(str(database) + suffix)
    monkeypatch.setattr("sys.argv", ["spy_5m_contiguous", "--db", str(database), "--staging-db", str(staging),
                                   "--volume-unit", "shares"])
    def no_tws(*args, **kwargs):
        pytest.fail("Invalid paths must be rejected before connecting to TWS.")
    monkeypatch.setattr(workflow, "SpyHistory", no_tws)
    assert workflow.main() == 1
    assert not list(tmp_path.iterdir())


def test_refresh_resumes_verified_tail_without_crossing_a_gap(workflow, stores):
    main, _ = stores
    run(workflow, stores, FakeHistory(), "2025-11-28")
    client = FakeHistory(gaps=["2025-12-02"])
    result = run(workflow, stores, client, "2025-12-03")
    assert (result["start"], result["through"], result["requested_through"], result["blocked"]) == (
        "2025-11-25", "2025-12-01", "2025-12-03", "2025-12-02",
    )
    assert all(day > "2025-11-28" for window, _ in client.requests for day in window)
    assert main.latest_date() == date(2025, 12, 1)
    repaired = FakeHistory()
    final = run(workflow, stores, repaired, "2025-12-03")
    assert final["through"] == "2025-12-03" and final["blocked"] is None
    assert all(day > "2025-12-01" for window, _ in repaired.requests for day in window)
    assert workflow.verify_published(main)["sessions"] == 6


def test_no_publication_when_latest_session_does_not_verify(workflow, stores):
    main, _ = stores
    result = run(workflow, stores, FakeHistory(gaps=["2025-11-28"]), "2025-11-28")
    assert result["blocked"] == "2025-11-28"
    assert result["through"] is None and main.latest_date() is None


def test_refresh_cannot_extend_a_damaged_published_range(workflow, stores):
    main, _ = stores
    run(workflow, stores, FakeHistory(), "2025-11-28")
    with main._connection(write=True) as db:
        db.execute("DELETE FROM bars WHERE time=(SELECT MIN(time) FROM bars)")
    with pytest.raises(ValueError, match="missing slot"):
        run(workflow, stores, FakeHistory(), "2025-12-01")
    assert main.latest_date() == date(2025, 11, 28)
    assert main.bounds()["requested_through"] == "2025-11-28"


@pytest.mark.parametrize("defect", ["conflict", "zero-volume", "unit"])
def test_atomic_publish_rejects_changed_staging_snapshot(workflow, stores, defect):
    main, stage = stores
    day = date(2025, 11, 28)
    window = sessions(day, day)
    session, = window
    main.configure(756733, "shares")
    stage.configure(756733, "lots" if defect == "unit" else "shares")
    rows = bars_for(spy_5m_store, session)
    stage.record(session, "complete", rows)
    assert stage.coverage(window)["unverified_sessions"] == 0
    if defect == "conflict":
        stage.record(session, "complete", [replace(rows[0], wap=101)])
    elif defect == "zero-volume":
        with stage._connection(write=True) as db:
            db.execute("UPDATE bars SET volume=0, wap=NULL")
    with pytest.raises(ValueError, match=r"conflict|VWAP|volume"):
        workflow.publish(main, stage, window, previous=None, requested=day)
    assert main.latest_date() is None
    assert main.target_end_date() is None
