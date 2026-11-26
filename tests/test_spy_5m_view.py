import json
from contextlib import contextmanager
from datetime import date
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from qqq_viewer.server import make_server
from qqq_viewer import spy_5m_store
from qqq_viewer.spy_5m_store import FiveMinuteBar, FiveMinuteStore, sessions
from tests.test_spy_5m_store import bars_for


@pytest.fixture
def spy_files(tmp_path):
    store = FiveMinuteStore(tmp_path / "spy5m.db")
    first, second = sessions(date(2025, 11, 26), date(2025, 11, 28))
    december = sessions(date(2025, 12, 1), date(2025, 12, 1))[0]
    store.configure(756733, "shares")
    store.set_target(december.date)
    for session in (first, second, december):
        rows = bars_for(spy_5m_store, session)
        if session == first:
            del rows[3]
        store.record(session, "complete", rows)
    daily = tmp_path / "spy.json"
    daily.write_text(json.dumps({"bars": [
        {"time": first.date.isoformat(), "open": 99.5, "close": 101, "upper": 110.123456, "lower": 93.25},
        {"time": second.date.isoformat(), "open": 100, "close": 101, "upper": 113, "lower": 90},
        {"time": december.date.isoformat(), "open": 100, "close": 101, "upper": 120, "lower": 90},
    ]}), encoding="utf-8")
    return store, daily, (first, second, december)


@contextmanager
def running_intraday(store, daily):
    with make_server(daily.with_suffix(".qqq.db"), 0, spy_cache=daily, spy_5m_db=store.path) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            worker.join()


def test_vwap_uses_wap_and_resets(spy_files):
    from qqq_viewer.spy_5m_view import month_payload

    store, daily, _ = spy_files
    payload = month_payload(store, daily, "2025-11")
    first, second = payload["sessions"][-2:]
    assert first["bars"][0]["vwap"] == second["bars"][0]["vwap"] == 100.5
    assert first["upper"] == 110.123456 and first["lower"] == 93.25
    assert first["bars"][0]["open"] == 100 and second["upper"] == 113


def test_vwap_stops_after_gaps_or_invalid_wap(tmp_path):
    from qqq_viewer.spy_5m_view import month_payload

    store = FiveMinuteStore(tmp_path / "spy.db")
    store.configure(756733, "shares")
    first, second = sessions(date(2025, 11, 26), date(2025, 11, 28))

    def bar(index, price, volume, session=first):
        return FiveMinuteBar(session.open_utc + 300 * index, 100, 130, 99, 110, volume, price)

    store.record(first, "complete", [
        bar(0, None, 0), bar(1, 100, 10), bar(2, None, 0), bar(3, 106, 20),
        bar(4, None, 3), bar(5, 108, 10),
    ])
    store.record(second, "complete", [bar(0, 120, 10, second), bar(2, 111, 10, second)])
    daily = tmp_path / "missing.json"
    payload = month_payload(store, daily, "2025-11")
    one, two = payload["sessions"][-2:]
    assert [row["vwap"] for row in one["bars"]] == [None, 100, 100, 104, None, None]
    assert [row["vwap"] for row in two["bars"]] == [120, None]
    assert one["upper"] is None and payload["daily_status"] == "missing"
    daily.write_text("{malformed", encoding="utf-8")
    assert month_payload(store, daily, "2025-11")["daily_status"] == "unreadable"


def test_intraday_api_routes_and_month_bounds(spy_files):
    store, daily, _ = spy_files
    with running_intraday(store, daily) as url:
        for path, title in (("/", b"<h1>QQQ</h1>"), ("/spy", b"<h1>SPY</h1>"),
                            ("/spy/5m", b"<h1>SPY</h1>")):
            with urlopen(url + path) as response:
                assert title in response.read()
        with urlopen(url + "/api/spy/5m/months") as response:
            months = json.load(response)
        assert months["months"][0] == "2000-01" and months["default_month"] == "2025-12"
        with urlopen(url + "/api/spy/5m/month/2025-11") as response:
            one, two = json.load(response)["sessions"][-2:]
        assert (len(one["bars"]), len(two["bars"])) == (77, 42)
        for month in ("1999-12", "2025-13", "9999-12"):
            with pytest.raises(HTTPError) as error:
                urlopen(url + "/api/spy/5m/month/" + month)
            assert error.value.code == 400


def test_price_scale_mismatch_in_replaced_daily_snapshot_hides_only_affected_levels(spy_files):
    from qqq_viewer.spy_5m_view import month_payload

    store, daily, (first, second, _) = spy_files
    payload = json.loads(daily.read_bytes())
    payload["bars"][0].update(open=100, close=101)
    payload["bars"][1].update(open=50, close=50.5)
    daily.write_text(json.dumps(payload), encoding="utf-8")
    month = month_payload(store, daily, "2025-11")
    by_day = {row["date"]: row for row in month["sessions"]}
    assert month["daily_status"] == "price_mismatch"
    assert by_day[first.date.isoformat()]["upper"] == 110.123456
    affected = by_day[second.date.isoformat()]
    assert (affected["upper"], affected["lower"]) == (None, None)
    assert affected["bars"][0]["vwap"] == 100.5


def test_published_bounds_exclude_out_of_scope_sessions_from_counts(spy_files):
    from qqq_viewer.spy_5m_view import month_index, month_payload

    store, daily, (_, early, december) = spy_files
    with store._connection(write=True) as db:
        db.execute("DELETE FROM bars WHERE date < ?", (early.date.isoformat(),))
        db.executemany("INSERT INTO metadata VALUES (?,?)", [
            ("verified_contiguous_start", early.date.isoformat()),
            ("verified_contiguous_end", december.date.isoformat()),
            ("requested_through", "2025-12-03"),
        ])
    index = month_index(store)
    assert index["months"] == ["2025-11", "2025-12"]
    november = month_payload(store, daily, "2025-11")
    assert november["coverage"]["expected"] == 42
    assert november["coverage"]["missing"] == 0
    assert november["published_start"] == "2025-11-28"
    assert november["published_through"] == "2025-12-01"
    assert november["requested_through"] == "2025-12-03"
    assert month_payload(store, daily, "2025-10")["sessions"] == []


@pytest.mark.parametrize("endpoint", ["open", "close"])
def test_missing_price_endpoint_withholds_unvalidated_daily_levels(tmp_path, endpoint):
    from qqq_viewer.spy_5m_view import month_payload

    store = FiveMinuteStore(tmp_path / "cache.db")
    session = sessions(date(2025, 11, 28), date(2025, 11, 28))[0]
    rows = bars_for(spy_5m_store, session)
    store.record(session, "complete", rows[1:] if endpoint == "open" else rows[:-1])
    daily = tmp_path / "spy.json"
    daily.write_text(json.dumps({"bars": [{
        "time": session.date.isoformat(), "open": 50, "close": 50.5, "upper": 55, "lower": 45,
    }]}), encoding="utf-8")
    record = month_payload(store, daily, "2025-11")["sessions"][-1]
    assert record["daily_level_status"] == "price_unverified"
    assert (record["upper"], record["lower"]) == (None, None)
    assert len(record["bars"]) == 41


def test_missing_and_malformed_daily_levels_keep_candles_and_valid_values(spy_files):
    from qqq_viewer.spy_5m_view import month_payload

    store, daily, (first, early, december) = spy_files
    document = json.loads(daily.read_bytes())
    document["bars"].pop(0)
    document["bars"][0]["upper"] = 10**400
    document["bars"][1]["lower"] = None
    daily.write_text(json.dumps(document), encoding="utf-8")
    nobody, invalid = month_payload(store, daily, "2025-11")["sessions"][-2:]
    assert nobody["date"] == first.date.isoformat() and nobody["daily_level_status"] == "no_daily_row"
    assert invalid["date"] == early.date.isoformat() and invalid["daily_level_status"] == "invalid_levels"
    assert (invalid["upper"], invalid["lower"], invalid["bars"][0]["vwap"]) == (None, 90, 100.5)
    valid = month_payload(store, daily, "2025-12")["sessions"][-1]
    assert valid["date"] == december.date.isoformat() and valid["daily_level_status"] == "missing_levels"
    assert (valid["upper"], valid["lower"]) == (120, None)


@pytest.mark.parametrize(("field", "value"), [
    ("upper", 10**100), ("upper", 99), ("lower", 110), ("lower", -1), ("lower", False),
])
def test_implausible_levels_are_rejected_individually(spy_files, field, value):
    from qqq_viewer.spy_5m_view import month_payload

    store, daily, _ = spy_files
    document = json.loads(daily.read_bytes())
    document["bars"][1][field] = value
    daily.write_text(json.dumps(document), encoding="utf-8")
    row = month_payload(store, daily, "2025-11")["sessions"][-1]
    assert row["daily_level_status"] == "invalid_levels" and row[field] is None
    assert row["lower" if field == "upper" else "upper"] is not None
