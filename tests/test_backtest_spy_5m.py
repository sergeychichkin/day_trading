import hashlib
import json
import os
from dataclasses import replace
from datetime import date, time
from pathlib import Path

import pytest

from qqq_viewer.spy_5m_store import FiveMinuteStore
from tests.test_spy_5m_replay import tape


@pytest.fixture
def runner():
    from qqq_viewer import backtest_spy_5m
    return backtest_spy_5m


@pytest.fixture
def replay_files(tmp_path):
    store = FiveMinuteStore(tmp_path / "spy.db")
    store.configure(756733, "shares")
    targets = ["2025-11-25", "2025-11-26", "2025-11-28", "2025-12-01"]
    for index, day in enumerate(targets):
        overrides = {4: (103, 105, 101, 104)}
        if index >= 2:
            overrides[0] = (104, 105, 98, 104)
        session, bars = tape(overrides, day=date.fromisoformat(day))
        store.record(session, "complete", bars)
    store.set_target(session.date)
    with store._connection(write=True) as db:
        db.execute("INSERT INTO metadata VALUES ('verified_contiguous_start', ?)", (targets[0],))
    daily = tmp_path / "daily.json"
    daily.write_text(json.dumps({"bars": [{"time": day, "open": 100, "high": 110, "low": 90, "close": 102,
                                           "upper": 101, "lower": 98} for day in targets]}), encoding="utf-8")
    config = tmp_path / "SPY.toml"
    config.write_text(f'ticker = "SPY"\nintraday_cache = "{store.path.name}"\n'
                      f'daily_cache = "{daily.name}"\n[exits]\nnext_session_open = true\n', encoding="utf-8")
    return store, daily, config


def test_config_defaults_and_validation(runner, tmp_path):
    cfg = runner.load_settings(Path("config/SPY.toml"))
    assert (cfg.entry.entry1_start_time, cfg.entry.entry1_end_time) == (time(9, 30), time(9, 40))
    assert (cfg.entry.entry_atr_buffer, cfg.entry.stop_atr_buffer, cfg.entry.entry_order_valid_minutes) == (0.25, 0.25, 120)
    assert (cfg.account.starting_balance, cfg.account.risk_pct, cfg.account.cash_cap) == (100000, 1, 1)
    assert cfg.same_day_close and not cfg.next_session_open
    assert cfg.intraday_cache == Path("data/spy_5m.sqlite3").resolve()
    path = tmp_path / "settings.toml"
    for custom, match in [
        ('[entry1]\nentry1_start_time="09:32"', "five-minute"),
        ('[entry1]\nentry1_end_time="09:25"', "window"),
        ('[entry1]\nentry_order_valid_minutes=12', "minutes"),
        ('[account]\nstarting_balance=nan', "finite"),
        ('[account]\ncash_cap=2', "cash_cap"),
        ('[exits]\nsame_day_close=false', "exit"),
    ]:
        path.write_text('ticker="SPY"\nintraday_cache="cache.db"\ndaily_cache="daily.json"\n' + custom, encoding="utf-8")
        with pytest.raises(ValueError, match=match):
            runner.load_settings(path)
    path.write_text('ticker="QQQ"\nintraday_cache="cache.db"\ndaily_cache="daily.json"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="SPY"):
        runner.load_settings(path)


def test_overlap_keeps_atr_and_ignores_current_daily_high_low_close(runner, replay_files):
    store, daily, config = replay_files
    cfg = runner.load_settings(config)
    november26, november28 = date(2025, 11, 26), date(2025, 11, 28)
    one = runner.run(cfg, november26, november28)
    two = runner.run(cfg, november28, november28)
    first, = two["scenarios"][0]["trades"]
    overlapping = [trade for trade in one["scenarios"][0]["trades"] if trade["date"] == "2025-11-28"][0]
    assert first["entry_trigger"] == overlapping["entry_trigger"]
    assert first["initial_stop"] == overlapping["initial_stop"]
    assert set(one["months"]) == {"2025-11", "2025-12"}  # December contains only a permitted exit tail.
    assert all(trade["date"] >= "2025-11-26" for trade in one["scenarios"][0]["trades"])
    payload = json.loads(daily.read_bytes())
    row = payload["bars"][2]
    row.update(high=500, low=1, close=400)
    daily.write_text(json.dumps(payload), encoding="utf-8")
    assert runner.run(cfg, november28, november28)["scenarios"][0]["trades"] == [first]
    row.update(open=90)
    daily.write_text(json.dumps(payload), encoding="utf-8")
    mismatch = runner.run(cfg, november28, november28)["scenarios"][0]
    assert mismatch["trades"] == [] and mismatch["skips"]["opening_price_mismatch"] == 1
    row.update(open=100, upper=None)
    daily.write_text(json.dumps(payload), encoding="utf-8")
    missing = runner.run(cfg, november28, november28)["scenarios"][0]
    assert missing["trades"] == [] and missing["skips"]["daily_upper_unavailable"] == 1
    with pytest.raises(ValueError, match="published"):
        runner.run(cfg, date(2025, 11, 24), november28)


def test_exit_modes_compound_independently_and_use_only_verified_next_open(runner, replay_files):
    store, daily, config = replay_files
    settings = runner.load_settings(config)
    result = runner.run(settings, date(2025, 11, 26), date(2025, 11, 28))
    same, overnight = result["scenarios"]
    assert (same["id"], overnight["id"]) == ("same_day_close", "next_session_open")
    assert same["equity"][0]["value"] == overnight["equity"][0]["value"] == 100000
    assert [t["exit_date"] for t in overnight["trades"]] == ["2025-11-28", "2025-12-01"]
    assert [t["date"] for t in overnight["trades"]] == ["2025-11-26", "2025-11-28"]
    assert same["trades"][0]["shares"] == overnight["trades"][0]["shares"]
    assert same["trades"][1]["shares"] != overnight["trades"][1]["shares"]
    assert result["months"]["2025-12"][0]["exit_only"]
    assert result["fingerprints"]["daily_sha256"] == hashlib.sha256(daily.read_bytes()).hexdigest()
    assert len(result["fingerprints"]["intraday_sha256"]) == 64
    boundary = runner.run(settings, date(2025, 12, 1), date(2025, 12, 1))["scenarios"][1]
    assert boundary["trades"] == [] and boundary["skips"]["next_open_unavailable"] == 1
    inputs = (settings.intraday_cache, settings.daily_cache)
    alias = daily.with_name("alias.html")
    os.link(daily, alias)
    for bad in (*inputs, Path(str(inputs[0]) + "-wal"), alias):
        with pytest.raises(ValueError, match="input"):
            runner.output_path(bad, settings, result["coverage"])
    assert runner.output_path(None, settings, result["coverage"]).name == "SPY_5m_entry1_2025-11-26_2025-11-28.html"
    with pytest.raises(ValueError, match="SPY"):
        runner.run(replace(settings, ticker="QQQ"))


@pytest.mark.parametrize("damage", ["missing", "conflict"])
def test_run_refuses_unverified_warmup_or_signal_input(runner, replay_files, damage):
    store, _, config = replay_files
    with store._connection(write=True) as db:
        if damage == "missing":
            db.execute("DELETE FROM bars WHERE time=(SELECT MIN(time) FROM bars)")
        else:
            db.execute("INSERT INTO conflicts SELECT time,date FROM bars ORDER BY time LIMIT 1")
    with pytest.raises(ValueError, match="Incomplete|Conflicting"):
        runner.run(runner.load_settings(config), date(2025, 11, 28), date(2025, 11, 28))
