import json
from contextlib import contextmanager
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from qqq_viewer.server import make_server


@pytest.fixture
def snapshot(tmp_path):
    bars = [
        {"time": day, "open": 100, "high": 106, "low": 97, "close": close, "volume": 800,
         "volume_sma_20": 750, "ema_20": ema, "ema_direction": direction, "upper": 104, "lower": 98}
        for day, close, ema, direction in [
            ("2000-01-31", 102, 101, "up"),
            ("2000-02-01", 99, 100.8, "down"),
            ("2000-04-03", 104, 101.1, "up"),
            ("2000-04-04", 100, 101, "down"),
        ]
    ]
    data = {"bars": bars, "missing_sessions": ["2000-04-05"], "through": "2000-04-05"}
    path = tmp_path / "spy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, data


@contextmanager
def running_spy_server(cache):
    server = make_server(0, spy_cache=cache)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_server_keeps_only_daily_viewer_routes_and_assets(snapshot):
    cache, data = snapshot
    with running_spy_server(cache) as url:
        with urlopen(f"{url}/") as response:
            assert response.url == f"{url}/spy"
        with urlopen(f"{url}/spy") as response:
            assert b"<h1>SPY</h1>" in response.read()
        with urlopen(f"{url}/api/spy/daily") as response:
            assert json.load(response) == data
        for path in ("/static/style.css", "/static/spy.js", "/static/vendor/lightweight-charts.js"):
            with urlopen(f"{url}{path}") as response:
                assert response.status == 200 and response.read()
        for path in ("/api/months", "/api/month/2020-01", "/spy/5m", "/api/spy/5m/months",
                     "/api/spy/5m/month/2020-01", "/static/chart.js", "/static/spy-5m.js"):
            with pytest.raises(HTTPError) as exc:
                urlopen(f"{url}{path}")
            assert exc.value.code == 404


def test_spy_api_reports_empty_cache(tmp_path):
    with running_spy_server(tmp_path / "absent.json") as url:
        with urlopen(f"{url}/api/spy/daily") as response:
            assert json.load(response) == {"bars": [], "missing_sessions": [], "through": None}
