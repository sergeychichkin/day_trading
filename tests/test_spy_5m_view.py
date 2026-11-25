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
        {"time": first.date.isoformat(), "upper": 110.123456, "lower": 93.25},
        {"time": second.date.isoformat(), "upper": 113, "lower": 90},
        {"time": december.date.isoformat(), "upper": 120, "lower": 90},
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
