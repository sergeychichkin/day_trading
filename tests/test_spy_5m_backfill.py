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

    async def request(contract, **options):
        assert (contract.conId, contract.primaryExchange) == (756733, "ARCA")
        assert options["endDateTime"] == "20000103-21:00:00"
        assert (options["durationStr"], options["barSizeSetting"], options["whatToShow"]) == (
            "1 D", "5 mins", "TRADES",
        )
        assert (options["useRTH"], options["formatDate"], options["keepUpToDate"]) == (True, 2, False)
        assert options["timeout"] == 25
        return [BarData(datetime.fromtimestamp(session.open_utc, timezone.utc),
                        100, 102, 99, 101, 50, 100.75, 9)]

    monkeypatch.setattr(history._ib, "reqContractDetailsAsync", details)
    monkeypatch.setattr(history._ib, "reqHistoricalDataAsync", request)
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
