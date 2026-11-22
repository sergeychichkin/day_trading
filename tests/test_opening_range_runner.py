from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from qqq_viewer.backtest_opening_range import load_settings, run, stats
from qqq_viewer.opening_range import EntrySettings, Settings
from qqq_viewer.store import Attempt, Store
from tests.test_opening_range import SESSION, day


def test_load_legacy_and_nested_entry_settings(tmp_path):
    path = tmp_path / "QQQ.toml"
    path.write_text('ticker = "QQQ"\ncache = "bars.db"\n[strategy]\nrisk_pct = 0.5\nentry_atr = 0.75\n')
    ticker, cache, legacy = load_settings(path)
    assert (ticker, cache) == ("QQQ", tmp_path / "bars.db")
    assert (legacy.entry1.enabled, legacy.entry2.enabled) == (True, False)
    assert legacy.entry1.risk_pct == .5
    assert legacy.entry1.entry_atr == .75
    path.write_text('ticker = "QQQ"\ncache = "bars.db"\n[entry1]\nenabled = false\n[entry2]\nenabled = true\nrisk_pct = 0.125\n')
    _, _, nested = load_settings(path)
    assert (nested.entry1.enabled, nested.entry2.enabled) == (False, True)
    assert nested.entry2.risk_pct == .125
    path.write_text('ticker = "QQQ"\ncache = "bars.db"\n[entry1]\nenabled = false\n[entry2]\nenabled = false\n')
    with pytest.raises(ValueError, match="enabled"):
        load_settings(path)


def test_scenarios_and_same_day_realized_equity(tmp_path):
    store = Store(tmp_path / "bars.db")
    bars = day({30: (101, 103, 101, 102), 31: (102, 104, 96, 102),
                32: (101, 105, 101, 104), 33: (104, 106, 103, 105),
                40: (96, 97, 94, 95)})
    store.record_attempt(SESSION, Attempt("complete", [b.to_raw() for b in bars], request={"symbol": "QQQ"}))
    cfg = Settings(entry2=EntrySettings())
    payload = run("QQQ", store, cfg, SESSION.date, SESSION.date)
    assert [case["id"] for case in payload["scenarios"]] == [
        "entry1-base", "entry2-base", "both-base", "entry1-stress", "entry2-stress", "both-stress",
    ]
    base = payload["scenarios"][0]
    assert len(base["trades"]) == 2
    assert [point["time"] for point in base["equity"]] == [
        SESSION.open_utc, bars[31].time, bars[40].time, SESSION.close_utc,
    ]
    cfg = replace(cfg, entry1=replace(cfg.entry1, enabled=False))
    only_two = run("QQQ", store, cfg, SESSION.date, SESSION.date)
    assert [case["id"] for case in only_two["scenarios"]] == ["entry2-base", "entry2-stress"]
    assert stats([], [98_000, 101_000, 101_000], 100_000)["max_drawdown_pct"] == 2.0


def test_output_reserves_original_baseline(monkeypatch, tmp_path):
    from qqq_viewer.backtest_opening_range import output_path

    monkeypatch.chdir(tmp_path)
    first, last = date(2020, 1, 1), date(2020, 12, 31)
    assert output_path("QQQ", first, last, None).name == "QQQ_opening_range_entries_2020-01-01_2020-12-31.html"
    old = Path("output/QQQ_opening_range_2020-01-01_2020-12-31.html")
    with pytest.raises(ValueError, match="baseline"):
        output_path("QQQ", first, last, old)
