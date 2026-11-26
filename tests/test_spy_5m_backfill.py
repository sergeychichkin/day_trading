import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from ib_async.objects import BarData

from qqq_viewer.spy_5m_store import FiveMinuteStore, completed
from qqq_viewer.tws import ContractIdentity
from tests.test_spy_5m_store import bars_for


@pytest.fixture
def module():
    from qqq_viewer import spy_5m_backfill

    return spy_5m_backfill


NOW = datetime(2000, 1, 19, 22, tzinfo=timezone.utc)


def test_spy_history_requests_native_rth_trade_bars(module, monkeypatch):
    history = module.SpyHistory(7497, 42, min_interval=0)
    session = completed(NOW)[0]
    symbol = SimpleNamespace(symbol="SPY", secType="STK", currency="USD", conId=756733,
                             primaryExchange="ARCA")
    monkeypatch.setattr(history._ib.client, "isReady", lambda: True)
    monkeypatch.setattr(history._ib.client, "serverVersion", lambda: 179)

    async def details(contract):
        assert (contract.symbol, contract.exchange, contract.currency) == ("SPY", "SMART", "USD")
        return [SimpleNamespace(contract=symbol)]

    def request(req_id, contract, end, duration, size, kind, rth, format_date, streaming, _):
        assert (contract.conId, contract.primaryExchange) == (756733, "ARCA")
        assert (end, duration, size, kind, rth, format_date, streaming) == (
            "20000103-21:00:00", "1 D", "5 mins", "TRADES", 1, 2, False,
        )
        history._ib.wrapper.historicalData(req_id, BarData(
            str(session.open_utc), 100, 102, 99, 101, 50, 100.75, 9,
        ))
        history._ib.wrapper.historicalDataEnd(req_id, "", "")

    monkeypatch.setattr(history._ib, "reqContractDetailsAsync", details)
    monkeypatch.setattr(history._ib.client, "getReqId", lambda: 9)
    monkeypatch.setattr(history._ib.client, "reqHistoricalData", request)
    contract = history.qualify_spy()
    status, rows, _ = history.request([session], "1 D")
    assert status == "complete" and rows[0].wap == 100.75
    symbol.currency = "CAD"
    with pytest.raises(ValueError, match="Expected one"):
        history.qualify_spy()


class FakeHistory:
    def __init__(self, module, download=None):
        self.module = module
        self.download = download or self.success
        self.requests = []

    def qualify_spy(self):
        return ContractIdentity(756733, "ARCA")

    def success(self, sessions):
        return "complete", [row for session in sessions for row in bars_for(self.module, session)], ""

    def request(self, sessions, duration):
        self.requests.append((sessions, duration))
        return self.download(sessions)


def run(module, store, client, tmp_path, *, probe=False):
    return module.run_backfill(store, client, NOW, volume_unit="shares", daily_cache=tmp_path / "daily.json",
                               probe_only=probe)


@pytest.mark.parametrize("failure", ["empty", "entitlement", "pacing", "disconnected"])
def test_empty_old_probe_stops_without_bulk_requests(module, tmp_path, failure):
    store = FiveMinuteStore(tmp_path / "spy.db")
    client = FakeHistory(module, lambda window: (failure, [], "broker detail"))
    result = run(module, store, client, tmp_path)
    assert len(client.requests) == 1
    assert "January 2000" in result["diagnostic"] and "broker detail" in result["diagnostic"]
    assert result["received"] == 0


def test_isolated_probe_gap_resumes_only_pending_sessions(module, tmp_path):
    store = FiveMinuteStore(tmp_path / "spy.db")
    client = FakeHistory(module)
    first = completed(NOW)[0]

    def download(window):
        if not client.requests[1:]:
            return client.success([first])
        status, rows, message = client.success(window)
        if first in window:
            rows = [replace(row, close=101.5) if row.time == first.open_utc else row for row in rows]
        return status, rows, message

    client.download = download
    result = run(module, store, client, tmp_path)
    assert result["missing"] == 0 and result["unverified_sessions"] == 0 and result["conflict_count"] == 0
    assert store.read_month("2000-01")["sessions"][0]["bars"][0]["close"] == 101
    assert len(client.requests) > 2
    resumed = FakeHistory(module, lambda window: pytest.fail("Already verified history re-requested"))
    assert run(module, store, resumed, tmp_path)["received"] == result["received"]


def test_recent_probe_failure_stops_before_bulk(module, tmp_path):
    store = FiveMinuteStore(tmp_path / "spy.db")
    client = FakeHistory(module)
    client.download = lambda window: (client.success(window) if len(window) > 1
                                      else ("empty", [], "no recent data"))
    result = run(module, store, client, tmp_path)
    assert "Recent" in result["diagnostic"] and "no recent data" in result["diagnostic"]
    assert len(client.requests) == 2


def test_cached_january_sample_survives_an_empty_retry(module, tmp_path):
    store = FiveMinuteStore(tmp_path / "spy.db")
    first = completed(NOW)[0]
    store.configure(756733, "shares")
    store.record(first, "complete", bars_for(module, first))
    client = FakeHistory(module)
    client.download = lambda window: (("empty", [], "no additional January rows")
                                      if len(client.requests) == 1 else client.success(window))
    result = run(module, store, client, tmp_path)
    assert result["diagnostic"] == ""
    assert result["missing"] == 0


def test_historical_deadline_cancels_the_broker_request(module, monkeypatch):
    session = completed(NOW)[0]
    history = module.SpyHistory(7497, 42, min_interval=0)
    history.identity = ContractIdentity(756733, "ARCA")
    history.timeout_seconds = 0.02
    api = history._ib.client
    cancelled = []
    monkeypatch.setattr(api, "isReady", lambda: True)
    monkeypatch.setattr(api, "getReqId", lambda: 9)
    monkeypatch.setattr(api, "reqHistoricalData", lambda *args: None)
    monkeypatch.setattr(api, "cancelHistoricalData", cancelled.append)
    run_original = module.util.run
    monkeypatch.setattr(module.util, "run", lambda task, timeout: run_original(task, timeout=min(timeout, 0.03)))
    status, rows, diagnostic = history.request([session], "1 D")
    assert status == "timeout" and rows == [] and "seconds" in diagnostic
    assert cancelled == [9]


def test_wrapper_drops_partial_buffer_after_broker_error(module, monkeypatch):
    session = completed(NOW)[0]
    history = module.SpyHistory(7497, 42, min_interval=0)
    history.identity = ContractIdentity(756733, "ARCA")
    api, wrapper = history._ib.client, history._ib.wrapper
    monkeypatch.setattr(api, "isReady", lambda: True)
    monkeypatch.setattr(api, "getReqId", lambda: 9)

    def request(req_id, *_):
        wrapper.historicalData(req_id, BarData(str(session.open_utc), 100, 102, 99, 101, 50, 100.75, 9))
        wrapper.error(req_id, 162, "HMDS query returned no data", "")

    monkeypatch.setattr(api, "reqHistoricalData", request)
    status, rows, _ = history.request([session], "1 D")
    assert (status, len(rows)) == ("empty", 1)
    assert 9 not in wrapper._results


def test_legacy_january_downloader_cannot_target_the_published_cache(module, tmp_path):
    store = FiveMinuteStore(tmp_path / "spy.db")
    store.set_target(completed(NOW)[0].date)
    with store._connection(write=True) as db:
        db.execute("INSERT INTO metadata VALUES ('verified_contiguous_start','2000-01-03')")
    client = FakeHistory(module)
    with pytest.raises(ValueError, match="spy_5m_contiguous"):
        run(module, store, client, tmp_path)
    assert not client.requests


@pytest.mark.parametrize("multiplier", [0.5, 1.001, None])
def test_systematic_price_basis_mismatch_blocks_backfill(module, tmp_path, multiplier):
    store = FiveMinuteStore(tmp_path / "spy.db")
    client = FakeHistory(module)
    if multiplier is not None:
        payload = {"bars": [{"time": s.date.isoformat(), "open": 100 * multiplier, "close": 101 * multiplier}
                            for s in completed(NOW)]}
        (tmp_path / "daily.json").write_text(json.dumps(payload), encoding="utf-8")
    result = run(module, store, client, tmp_path)
    if multiplier == 0.5:
        assert "price-basis mismatch" in result["diagnostic"]
        assert result["received"] == 0 and len(client.requests) == 2
    else:
        assert result["missing"] == 0
        assert ("unavailable" if multiplier is None else "comparable") in result["price_check"]
