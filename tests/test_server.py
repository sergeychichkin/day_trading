import json
from contextlib import contextmanager
from datetime import date
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from qqq_viewer.server import make_server, month_payload
from qqq_viewer.sessions import sessions
from qqq_viewer.store import Attempt, Bar, Store


@pytest.fixture
def market_cache(tmp_path):
    store = Store(tmp_path / "market.db")
    january = sessions(date(2020, 1, 31), date(2020, 1, 31))[0]
    february = sessions(date(2020, 2, 3), date(2020, 2, 3))[0]
    store.set_target(date(2020, 3, 9))
    first = [Bar(t, 215, 216, 214, 215.5, 100) for t in january.minutes]
    for i, bar in enumerate(first[-19:], 1):
        first[-20 + i] = Bar(bar.time, 215, 216, 214, 215.5, i)
    next_month = [Bar(t, 220, 221, 219, 220.5, 20) for t in february.minutes]
    store.record_attempt(january, Attempt("complete", [bar.to_raw() for bar in first]))
    store.record_attempt(february, Attempt("complete", [bar.to_raw() for bar in next_month]))
    return store


@contextmanager
def running_server(store):
    server = make_server(store.path, 0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_month_only_returns_selected_bars_and_continuous_sma(market_cache):
    february = month_payload(market_cache, "2020-02")
    bar = february["bars"][0]
    assert bar["time"] == 1580740200
    assert bar["volume_sma_20"] == 10.5
    assert len(february["bars"]) == 390
    assert february["coverage"]["missing"] == 7020
    assert february["coverage"]["status"] == "incomplete"


def test_month_api_validation_and_empty_month(market_cache):
    with running_server(market_cache) as url:
        with urlopen(f"{url}/api/months") as response:
            months = json.load(response)
        assert months["default_month"] == "2020-02"
        assert months["months"] == ["2020-01", "2020-02", "2020-03"]
        with urlopen(f"{url}/api/month/2020-03") as response:
            march = json.load(response)
            assert march["bars"] == []
            assert march["coverage"]["status"] == "empty"
        with pytest.raises(HTTPError) as error:
            urlopen(f"{url}/api/month/2020-13")
        assert error.value.code == 400


def test_month_identifies_the_conflicting_minute(market_cache):
    february = sessions(date(2020, 2, 3), date(2020, 2, 3))[0]
    market_cache.record_attempt(
        february, Attempt("complete", [(str(february.open_utc), "220", "221", "219", "220.5", "21")]),
    )
    payload = month_payload(market_cache, "2020-02")
    assert payload["bars"][0]["volume"] == 20
    assert payload["conflict_times"] == [february.open_utc]
    assert payload["coverage"]["conflicting"] == 1
