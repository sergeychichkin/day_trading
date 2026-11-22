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
    scenario_ids = ["entry1-base", "entry2-base", "both-base", "entry1-stress", "entry2-stress", "both-stress"]
    assert [(case["id"], case["rule_version"], case["scenario_id"]) for case in payload["scenarios"]] == [
        (f"{version}-{scenario}", version, scenario)
        for version in ("legacy", "revised") for scenario in scenario_ids
    ]
    base = payload["scenarios"][0]
    assert len(base["trades"]) == 2
    assert [point["time"] for point in base["equity"]] == [
        SESSION.open_utc, bars[31].time, bars[40].time, SESSION.close_utc,
    ]
    cfg = replace(cfg, entry1=replace(cfg.entry1, enabled=False))
    only_two = run("QQQ", store, cfg, SESSION.date, SESSION.date)
    assert [case["id"] for case in only_two["scenarios"]] == [
        "legacy-entry2-base", "legacy-entry2-stress", "revised-entry2-base", "revised-entry2-stress",
    ]
    for case in only_two["scenarios"]:
        assert case["equity"] == [{"time": SESSION.open_utc, "value": 100_000},
                                  {"time": SESSION.close_utc, "value": 100_000}]
    assert stats([], [98_000, 101_000, 101_000], 100_000)["max_drawdown_pct"] == 2.0


def test_output_reserves_original_baseline(monkeypatch, tmp_path):
    from qqq_viewer.backtest_opening_range import output_path

    monkeypatch.chdir(tmp_path)
    first, last = date(2020, 1, 1), date(2020, 12, 31)
    assert output_path("QQQ", first, last, None).name == "QQQ_opening_range_rule_comparison_2020-01-01_2020-12-31.html"
    old = Path("output/QQQ_opening_range_2020-01-01_2020-12-31.html")
    with pytest.raises(ValueError, match="baseline"):
        output_path("QQQ", first, last, old)
    old_comparison = Path("output/QQQ_opening_range_entries_2020-01-01_2020-12-31.html")
    with pytest.raises(ValueError, match="previous comparison"):
        output_path("QQQ", first, last, old_comparison)


def test_protects_checkout_baseline_from_another_working_directory(monkeypatch, tmp_path):
    from qqq_viewer import backtest_opening_range as cli

    original = Path(cli.__file__).resolve().parents[1] / cli.LEGACY_BASELINE
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="baseline"):
        cli.output_path("QQQ", cli.DEFAULT_START, cli.DEFAULT_END, original)


def test_rejects_input_cache_as_report_destination(tmp_path):
    from qqq_viewer.backtest_opening_range import output_path

    cache = tmp_path / "input.sqlite3"
    cache.write_bytes(b"fixture")
    first, last = date(2020, 1, 1), date(2020, 12, 31)
    with pytest.raises(ValueError, match="input cache"):
        output_path("QQQ", first, last, cache, cache=cache)
    assert cache.read_bytes() == b"fixture"


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_protects_sqlite_sidecars_as_report_destinations(tmp_path, suffix):
    from qqq_viewer.backtest_opening_range import output_path

    cache = tmp_path / "input.sqlite3"
    dest = cache.with_name(cache.name + suffix)
    with pytest.raises(ValueError, match="input cache"):
        output_path("QQQ", SESSION.date, SESSION.date, dest, cache=cache)
    assert not dest.exists()


def test_protects_hardlink_to_sqlite_sidecar(tmp_path):
    from qqq_viewer.backtest_opening_range import output_path

    cache = tmp_path / "input.sqlite3"
    sidecar = cache.with_name(cache.name + "-wal")
    sidecar.write_bytes(b"fixture")
    alias = tmp_path / "alias.html"
    alias.hardlink_to(sidecar)
    with pytest.raises(ValueError, match="input cache"):
        output_path("QQQ", SESSION.date, SESSION.date, alias, cache=cache)
    assert sidecar.read_bytes() == b"fixture"


@pytest.mark.parametrize("origin_request,completion_bounds,message", [
    ({}, "correct", "source"),
    ({"symbol": "QQQ"}, "shifted", "completion"),
    ({"symbol": "QQQ"}, "incorrect_ticker", "completion"),
])
def test_rejects_unverified_canonical_sources_and_completion(tmp_path, origin_request, completion_bounds, message):
    from qqq_viewer.sessions import Session

    store = Store(tmp_path / "cache.sqlite3")
    store.record_attempt(SESSION, Attempt("timeout", [b.to_raw() for b in day({})], request=origin_request))
    completion = SESSION
    request = {"symbol": "QQQ"}
    if completion_bounds == "shifted":
        completion = Session(SESSION.date, SESSION.open_utc + 60, SESSION.close_utc)
    if completion_bounds == "incorrect_ticker":
        request = {}
    store.record_attempt(completion, Attempt("complete", [], request=request))
    with pytest.raises(ValueError, match=message):
        run("QQQ", store, Settings(), SESSION.date, SESSION.date)
