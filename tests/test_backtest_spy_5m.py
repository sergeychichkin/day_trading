import hashlib
import json
import os
from dataclasses import replace
from datetime import date, time, timedelta
from pathlib import Path

import pytest

from qqq_viewer.spy_5m_store import FiveMinuteStore, sessions
from tests.test_spy_5m_replay import tape

DAILY_ID = {"schema": "yahoo-daily-v1", "ticker": "SPY", "source": "Yahoo Finance"}


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
            overrides[0] = (104, 104, 98, 104)
        session, bars = tape(overrides, day=date.fromisoformat(day))
        store.record(session, "complete", bars)
    store.set_target(session.date)
    with store._connection(write=True) as db:
        db.execute("INSERT INTO metadata VALUES ('verified_contiguous_start', ?)", (targets[0],))
    daily = tmp_path / "daily.json"
    daily.write_text(json.dumps({**DAILY_ID, "bars": [{"time": day, "open": 100, "high": 110, "low": 90, "close": 102,
                                           "upper": 101, "lower": 98} for day in targets]}), encoding="utf-8")
    config = tmp_path / "SPY.toml"
    config.write_text(f'ticker = "SPY"\nintraday_cache = "{store.path.name}"\n'
                      f'daily_cache = "{daily.name}"\n[exits]\nnext_session_open = true\n', encoding="utf-8")
    return store, daily, config


def test_config_defaults_and_validation(runner, tmp_path):
    cfg = runner.load_settings(Path("config/SPY.toml"))
    assert (cfg.entry.entry1_start_time, cfg.entry.entry1_end_time) == (time(9, 30), time(9, 40))
    assert cfg.entry.entry_atr_buffer == 0.25
    assert (cfg.account.starting_balance, cfg.account.risk_pct, cfg.account.cash_cap) == (100000, 1, 1)
    assert cfg.trailing_stop and not (cfg.same_day_close or cfg.next_session_open)
    assert (cfg.chart.high_atr_multiplier, cfg.chart.low_atr_multiplier) == (0.25, 0.5)
    assert cfg.intraday_cache == Path("data/spy_5m.sqlite3").resolve()
    path = tmp_path / "settings.toml"
    for custom, match in [
        ('[entry1]\nentry1_start_time="09:32"', "five-minute"),
        ('[entry1]\nentry1_end_time="09:25"', "window"),
        ('[account]\nstarting_balance=nan', "finite"),
        ('[account]\ncash_cap=2', "cash_cap"),
        ('[exits]\nsame_day_close=false', "exit"),
        ('[chart]\nlow_atr_multiplier=-1', "negative"),
    ]:
        path.write_text('ticker="SPY"\nintraday_cache="cache.db"\ndaily_cache="daily.json"\n' + custom, encoding="utf-8")
        with pytest.raises(ValueError, match=match):
            runner.load_settings(path)
    path.write_text('ticker="QQQ"\nintraday_cache="cache.db"\ndaily_cache="daily.json"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="SPY"):
        runner.load_settings(path)


def test_legacy_order_lifetime_loads_with_notice(runner, replay_files):
    _, _, config = replay_files
    with config.open("a", encoding="utf-8") as stream:
        stream.write("[entry1]\nentry_order_valid_minutes=120\n")
    with pytest.warns(UserWarning, match="09:35 ET next session"):
        runner.load_settings(config)


@pytest.mark.parametrize("outcome", ["filled", "expired", "rejected"])
def test_runner_tracks_overnight_order_across_month_and_cutoff(runner, replay_files, outcome):
    store, _, config = replay_files
    friday, monday = map(date.fromisoformat, ("2025-11-28", "2025-12-01"))
    friday_session, monday_session = sessions(friday, monday)
    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET open=102,high=104,close=103 WHERE time=?", (friday_session.open_utc + 1200,))
        opening, high = {"filled": (105, 106), "expired": (104, 104), "rejected": (800, 801)}[outcome]
        db.execute("UPDATE bars SET open=?,high=?,close=? WHERE time=?", (opening, high, opening, monday_session.open_utc))
        db.execute("UPDATE bars SET high=107 WHERE time=?", (monday_session.open_utc + 300,))
    cfg = runner.load_settings(config)
    short = runner.run(cfg, friday, friday)["scenarios"][0]
    assert short["pending_entry"]["expiry_time"] == monday_session.open_utc + 300
    assert short["pending_entry"]["mark_time"] == friday_session.close_utc - 300
    assert not any(event["action"] in {"filled", "expired"} for event in short["events"])
    full = runner.run(cfg, friday, monday)["scenarios"]
    for case in full:
        submitted = next(event for event in case["events"] if event["action"] == "submitted")
        order_events = [event for event in case["events"] if event.get("order_id") == submitted["order_id"]]
        terminal = order_events[-1]
        assert terminal["action"] == outcome
        assert terminal["trigger"] == submitted["trigger"]
        assert terminal["time"] == monday_session.open_utc + (300 if outcome == "expired" else 0)
    if outcome == "filled":
        assert full[0]["trades"][0]["date"] == str(monday)
        assert full[0]["trades"][0]["holding_days"] == 1
        assert full[0]["trades"][0]["initial_stop"] == 98


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


def test_runner_uses_plotted_daily_lower_or_skips(runner, replay_files):
    _, daily, config = replay_files
    day = date(2025, 11, 28)
    settings = runner.load_settings(config)
    payload = json.loads(daily.read_bytes())
    row = next(row for row in payload["bars"] if row["time"] == day.isoformat())
    row["lower"] = 98.009
    daily.write_text(json.dumps(payload), encoding="utf-8")
    data = runner.run(settings, day, day)
    assert data["months"]["2025-11"][0]["lower"] == 98.009
    assert all(case["trades"][0]["initial_stop"] == 98.0 for case in data["scenarios"])

    for lower in (None, 101):
        row["lower"] = lower
        daily.write_text(json.dumps(payload), encoding="utf-8")
        data = runner.run(settings, day, day)
        assert all(not case["trades"] and case["skips"]["daily_lower_unavailable"] == 1
                   for case in data["scenarios"])


def test_legacy_stop_atr_buffer_warns_and_is_ignored(runner, replay_files):
    _, _, config = replay_files
    with config.open("a", encoding="utf-8") as stream:
        stream.write("[entry1]\nstop_atr_buffer = 0.25\n")
    with pytest.warns(UserWarning, match="stop_atr_buffer.*ignored"):
        settings = runner.load_settings(config)
    assert not hasattr(settings.entry, "stop_atr_buffer")


def test_trade_daily_stats_use_previous_nyse_session_and_reset_at_gaps(runner, replay_files):
    _, daily, config = replay_files
    friday = date(2025, 11, 28)
    history = runner.sessions(friday - timedelta(days=42), friday - timedelta(days=1))[-24:]
    rows = [dict(time=session.date.isoformat(), open=100, high=110, low=90, close=102, volume=100,
                 ema_direction="down", upper=101, lower=98) for session in history]
    rows[-1].update(volume=200)
    rows.append(dict(rows[-1], time=friday.isoformat(), close=109, ema_direction="up", volume=99999))

    def previous_stats():
        daily.write_text(json.dumps({**DAILY_ID, "bars": rows}), encoding="utf-8")
        data = runner.run(runner.load_settings(config), friday, friday)
        return data["scenarios"][0]["trades"][0]["prior_daily"]

    rows[-7]["ema_direction"] = "up"
    assert previous_stats() == {"date": "2025-11-26", "ema20_streak": -5,
                                "volume_sma20_ratio": pytest.approx(200 / 105),
                                "body_atr20_ratio": 0.1, "atr20_close_pct": pytest.approx(100 * 20 / 102)}
    rows[-2]["ema_direction"] = "up"
    assert previous_stats()["ema20_streak"] == 1
    del rows[-2]
    assert previous_stats() is None


@pytest.mark.parametrize("kind", [
    "existing", "config alias", "input", "extension", "file ancestor",
    pytest.param("reserved", marks=pytest.mark.skipif(os.name != "nt", reason="Windows path rules")),
    pytest.param("wildcard", marks=pytest.mark.skipif(os.name != "nt", reason="Windows path rules")),
])
def test_cli_rejects_invalid_output_before_running(runner, replay_files, monkeypatch, capsys, kind):
    store, _, config = replay_files
    output = config.with_name("report.html")
    if kind == "existing":
        output.write_text("keep me", encoding="utf-8")
    elif kind == "config alias":
        os.link(config, output)
    elif kind == "input":
        output = store.path
    elif kind == "extension":
        output = output.with_suffix(".txt")
    elif kind == "file ancestor":
        output = config / "report.html"
    else:
        output = output.with_name("NUL.html" if kind == "reserved" else "bad*.html")
    protected = config if kind == "file ancestor" else output if kind in {"existing", "config alias", "input"} else None
    original = protected.read_bytes() if protected is not None else None
    monkeypatch.setattr(runner, "run", lambda *args: pytest.fail("invalid path reached replay"))
    assert runner.main(["--config", str(config), "--output", str(output)]) == 1
    assert {"existing": "already exists", "config alias": "settings file",
            "input": "input cache", "extension": ".html file", "file ancestor": "not a directory",
            "reserved": "reserved or invalid Windows name", "wildcard": "reserved or invalid Windows name",
            }[kind] in capsys.readouterr().err
    if protected is not None:
        assert protected.read_bytes() == original


def test_cli_default_output_and_toml_times(runner, replay_files, monkeypatch, capsys):
    _, _, config = replay_files
    monkeypatch.chdir(config.parent)
    with config.open("a", encoding="utf-8") as stream:
        stream.write('[entry1]\nentry1_start_time="09:35"\nentry1_end_time="09:45"\n'
                     '[chart]\nhigh_atr_multiplier=0.75\n')
    args = ["--config", str(config), "--start", "2025-11-28", "--end", "2025-11-28"]
    for flag in ("--entry1-start-time", "--entry1-end-time"):
        with pytest.raises(SystemExit) as failure:
            runner.main([*args, flag, "09:40"])
        assert failure.value.code == 2
    output = Path("output/SPY_5m_entry1_2025-11-28_2025-11-28.html")
    output.parent.mkdir()
    output.write_text("previous report", encoding="utf-8")
    assert runner.main(args) == 0
    payload = json.loads(output.read_text(encoding="utf-8")
                         .split('id="report-data" type="application/json">', 1)[1].split("</script>", 1)[0])
    entry = payload["settings"]["entry"]
    assert (entry["entry1_start_time"], entry["entry1_end_time"]) == ("09:35", "09:45")
    assert payload["settings"]["chart"]["high_atr_multiplier"] == 0.75
    assert "1 trades" in capsys.readouterr().out


def test_default_output_preserves_existing_report_if_replacement_fails(
    runner, replay_files, monkeypatch, capsys,
):
    _, _, config = replay_files
    monkeypatch.chdir(config.parent)
    output = Path("output/SPY_5m_entry1_2025-11-28_2025-11-28.html")
    output.parent.mkdir()
    output.write_text("previous report", encoding="utf-8")

    def fail_replace(source, target):
        assert target == output.resolve()
        assert source.parent == target.parent
        raise PermissionError("replacement unavailable")

    monkeypatch.setattr(Path, "replace", fail_replace)
    assert runner.main(["--config", str(config), "--start", "2025-11-28", "--end", "2025-11-28"]) == 1
    assert "replacement unavailable" in capsys.readouterr().err
    assert output.read_text(encoding="utf-8") == "previous report"
    assert list(output.parent.iterdir()) == [output]


@pytest.mark.parametrize("value", ["09:30:00", "09:32:00", "09:30:01"])
def test_native_toml_time_uses_same_grid_validation(runner, replay_files, value):
    _, _, config = replay_files
    with config.open("a", encoding="utf-8") as stream:
        stream.write(f"[entry1]\nentry1_start_time={value}\n")
    if value == "09:30:00":
        assert runner.load_settings(config).entry.entry1_start_time == time(9, 30)
    else:
        with pytest.raises(ValueError, match="without seconds or a time-zone offset"):
            runner.load_settings(config)


def test_reversed_publication_is_reported_as_invalid(runner, replay_files):
    store, _, config = replay_files
    with store._connection(write=True) as db:
        db.execute("UPDATE metadata SET value='2025-12-02' WHERE key='verified_contiguous_start'")
    with pytest.raises(ValueError, match="Invalid published SPY coverage bounds"):
        runner.run(runner.load_settings(config))


def test_missing_unpublished_tail_is_not_reported_as_damage(runner, replay_files):
    _, _, config = replay_files
    end = date(2025, 12, 1)
    data = runner.run(runner.load_settings(config), end, end)
    assert data["coverage"]["next_open_tail_unavailable"] == "No verified session in cache after 2025-12-01."


def test_partial_unpublished_tail_is_reported_as_damage(runner, replay_files):
    store, _, config = replay_files
    with store._connection(write=True) as db:
        db.execute("UPDATE metadata SET value='2025-11-28' WHERE key='target'")
        db.execute("DELETE FROM bars WHERE date='2025-12-01'")
    friday = date(2025, 11, 28)
    data = runner.run(runner.load_settings(config), friday, friday)
    assert "Incomplete" in data["coverage"]["next_open_tail_unavailable"]


def test_empty_next_session_lookup_preserves_same_day_run(runner, replay_files, monkeypatch):
    _, _, config = replay_files
    real_sessions = runner.sessions
    end = date(2025, 12, 1)
    monkeypatch.setattr(runner, "sessions", lambda first, last: [] if first > end else real_sessions(first, last))
    data = runner.run(runner.load_settings(config), end, end)
    assert data["scenarios"][0]["stats"]["trades"] == 1
    assert data["coverage"]["next_open_tail_unavailable"] == "No next NYSE session found in the exchange calendar lookup."


@pytest.mark.parametrize(("pnls", "expected"), [
    ([30, -10, 0, -5], 2.0), ([30, 0], "infinite"), ([-10, 0], 0.0), ([0], None), ([], None),
])
def test_profit_factor_uses_realized_net_trade_pnl(runner, pnls, expected):
    case = {"equity": [], "trades": [
        dict(pnl=pnl, ambiguous=False, commission=1, slippage=0.5, gap_slippage=0) for pnl in pnls
    ]}
    assert runner._stats(case, 100)["profit_factor"] == expected


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


def shared_stop_daily_rows():
    days = sessions(date(2025, 10, 1), date(2025, 12, 3))
    rows = {str(day.date): {"time": str(day.date), "open": 104, "high": 105, "low": 102,
                           "close": 104.5, "ema_direction": "up", "lower": 98, "upper": 104.1}
            for day in days}
    for day in ("2025-11-25", "2025-11-26", "2025-11-28", "2025-12-01"):
        rows[day]["close"] = 103
    rows["2025-11-28"].update(low=101, ema_direction="down")
    rows["2025-12-01"].update(ema_direction="down")
    return rows


def test_daily_stop_candidates_use_completed_trigger_day_and_do_not_repeat(runner, replay_files):
    _, _, config = replay_files
    daily = shared_stop_daily_rows()
    end = date(2025, 12, 3)
    cfg = runner.load_settings(config)
    rules = ((4, runner.DailyStopSettings()), (5, runner.DailyStopSettings(2, 0)))
    _, opening = runner._daily_context(daily, end, rules, cfg.account.tick_size)
    assert opening["2025-11-28"] == opening["2025-12-02"] == ()
    fourth, fifth = opening["2025-12-01"]
    friday = sessions(date(2025, 11, 28), date(2025, 11, 28))[0]
    assert fourth.source == 4 and fourth.trigger_time == friday.close_utc
    assert fourth.price == 100.23
    assert fifth.source == 5 and fifth.price == 101
    extreme = ((4, runner.DailyStopSettings(4, 1e308)),)
    assert runner._daily_context(daily, end, extreme, 0.01)[1]["2025-12-01"] == ()
    daily["2025-12-01"].update(high=900, low=1)
    assert runner._daily_context(daily, end, rules, 0.01)[1]["2025-12-01"] == (fourth, fifth)
    for direction in ("neutral", None):
        daily["2025-11-26"]["ema_direction"] = direction
        assert runner._daily_context(daily, end, rules, 0.01)[1]["2025-12-01"] == (fourth,)
    del daily["2025-11-26"]
    assert runner._daily_context(daily, end, rules, 0.01)[1]["2025-12-01"] == ()


def test_managed_scenario_defaults_and_daily_enable_switches(runner, replay_files):
    _, _, config = replay_files
    standard = runner.load_settings(Path("config/SPY.toml"))
    assert (standard.trailing_stop, standard.exit4, standard.exit5) == (True, True, True)
    assert (standard.exit3_max_holding_days, standard.exit4_settings, standard.exit5_settings) == (
        9, runner.DailyStopSettings(4, 0.25), runner.DailyStopSettings(4, 0.25),
    )
    legacy = replace(runner.load_settings(config), trailing_stop=True, same_day_close=False, next_session_open=False)
    assert not legacy.exit4 and not legacy.exit5
    for settings in (legacy, replace(legacy, trailing_stop=False, exit4=True), replace(legacy, trailing_stop=False, exit5=True)):
        assert [case["id"] for case in runner.run(settings)["scenarios"]] == ["trailing_stop"]


def test_runner_applies_daily_raise_only_to_carried_position(runner, replay_files):
    store, daily, config = replay_files
    rows = shared_stop_daily_rows()
    daily.write_text(json.dumps({**DAILY_ID, "bars": list(rows.values())}), encoding="utf-8")
    wednesday, friday, monday = map(date.fromisoformat, ("2025-11-26", "2025-11-28", "2025-12-01"))
    following = sessions(monday, monday)[0]
    with store._connection(write=True) as db:
        for session in sessions(wednesday, friday):
            for index, values in enumerate(((104, 104, 99, 104, 104), (104, 104, 103, 104, 104),
                                             (104, 105, 104, 104.8, 105), (104.8, 105, 104.1, 104.5, 104.5),
                                             (105, 107, 104, 106, 105.5))):
                db.execute("UPDATE bars SET open=?,high=?,low=?,close=?,wap=? WHERE time=?",
                           (*values, session.open_utc + 300 * index))
        db.execute("UPDATE bars SET open=99,low=98 WHERE time=?", (following.open_utc,))
    cfg = replace(runner.load_settings(config), same_day_close=False, next_session_open=False, exit4=True, exit5=True)
    case, = runner.run(cfg, wednesday, monday)["scenarios"]
    trade, = case["trades"]
    assert trade["exit_time"] == following.open_utc and trade["stop_source"] == 4 and trade["holding_days"] == 3
    assert trade["stop_adjustments"][0]["candle_time"] < trade["exit_time"]

    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET open=104,high=105,close=105,wap=104.5 "
                   "WHERE time=(SELECT MIN(time)+1200 FROM bars WHERE date=?)", (str(friday),))
        db.execute("UPDATE bars SET open=105,high=106,low=102,close=105 WHERE time=?", (following.open_utc,))
    case, = runner.run(cfg, friday, monday)["scenarios"]
    position = case["open_position"]
    assert position["entry_date"] == str(monday) and position["active_stop"] == position["initial_stop"] == 98
    assert position["stop_adjustments"] == []


def test_exit3_resolves_exact_prior_sessions_and_warmup_volume(runner, replay_files):
    store, daily, config = replay_files
    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET open=94, low=93 WHERE date='2025-11-25' "
                   "AND time=(SELECT MIN(time) FROM bars WHERE date='2025-11-25')")
        db.execute("UPDATE bars SET volume=40 WHERE date IN ('2025-11-25', '2025-11-26')")
    payload = json.loads(daily.read_bytes())
    payload["bars"].insert(0, {"time": "2025-11-24", "open": 100, "lower": 99})
    payload["bars"] = [row for row in payload["bars"] if row["time"] != "2025-11-26"]
    daily.write_text(json.dumps(payload), encoding="utf-8")
    settings = replace(runner.load_settings(config), trailing_stop=True, next_session_open=False)
    full = runner.run(settings)
    day25, day26, friday = full["months"]["2025-11"]
    monday, = full["months"]["2025-12"]
    assert day25["prior_lowers"][0] == {
        "date": "2025-11-24", "lower": 99, "status": "daily_only_before_intraday_coverage",
    }
    assert day26["prior_lowers"][0]["status"] == "opening_price_mismatch"
    assert friday["prior_lowers"] == [
        {"date": "2025-11-26", "lower": None, "status": "daily_lower_unavailable"},
        {"date": "2025-11-25", "lower": None, "status": "opening_price_mismatch"},
    ]
    assert [row["date"] for row in monday["prior_lowers"]] == ["2025-11-28", "2025-11-26"]
    subset = runner.run(settings, date(2025, 11, 28))
    assert subset["months"]["2025-11"] == [friday]
    assert subset["months"]["2025-12"] == [monday]
    assert friday["bars"][0]["volume_sma_20"] == (19 * 40 + 10) / 20


def test_exit3_entry_lower_prevents_first_following_raise_and_ignores_legacy_tail(runner, replay_files):
    store, daily, config = replay_files
    wednesday = date(2025, 11, 26)
    friday = date(2025, 11, 28)
    session, _ = tape(day=friday)
    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET low=99 WHERE time=?", (session.open_utc,))
        db.execute("UPDATE bars SET open=101.1,high=102,low=101,close=101.2 WHERE time>=? AND time<?",
                   (session.open_utc + 1500, session.close_utc))
    payload = json.loads(daily.read_bytes())
    payload["bars"][2].update(open=101, lower=100.9)
    daily.write_text(json.dumps(payload), encoding="utf-8")
    settings = replace(runner.load_settings(config), trailing_stop=True, same_day_close=False)
    entry_case = runner.run(settings, wednesday, wednesday)["scenarios"][1]
    entry_position = entry_case["open_position"]
    assert entry_position["stop_adjustments"] == []
    assert entry_position["pending_stop"] is None
    assert entry_position["active_stop"] == entry_position["initial_stop"]
    result = runner.run(settings, wednesday, friday)
    legacy, trailing = result["scenarios"]
    assert result["coverage"]["next_open_tail"] == "2025-12-01"
    coverage = result["coverage"]
    assert coverage["exit_only_bars"] == 78
    assert coverage["fingerprinted_bars"] == coverage["warmup_bars"] + coverage["signal_bars"] + 78
    assert legacy["trades"][-1]["exit_date"] == "2025-12-01"
    assert trailing["trades"] == [] and trailing["stats"]["net_pnl"] == 0
    position = trailing["open_position"]
    assert position["active_stop"] == position["initial_stop"] == 98
    assert position["pending_stop"] is None
    assert position["stop_adjustments"] == []
    assert position["mark_time"] == session.close_utc
    assert position["unrealized_pnl"] == pytest.approx(
        (101.2 - position["entry_price"]) * position["shares"] - max(1, position["shares"] * 0.005),
    )
    monday = date(2025, 12, 1)
    monday_session, monday_bars = tape({
        3: (100, 100.8, 99.8, 100.7), 4: (97, 105, 96, 104),
        5: (104, 105, 99, 101), 6: (107, 108, 94, 106),
    }, day=monday)
    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET low=99 WHERE time=?", (monday_session.open_utc,))
        db.execute("UPDATE bars SET open=100,high=100.8,low=99.8,close=100.7 WHERE time=?",
                   (monday_session.close_utc - 300,))
    cutoff = runner.run(settings, wednesday, monday)["scenarios"][1]["open_position"]
    assert cutoff["active_stop"] == 98 < cutoff["pending_stop"]
    assert cutoff["stop_adjustments"] == []

    with store._connection(write=True) as db:
        db.executemany("UPDATE bars SET open=?,high=?,low=?,close=? WHERE time=?", [
            (bar.open, bar.high, bar.low, bar.close, bar.time) for bar in monday_bars[3:7]
        ])
    settings = replace(settings, entry=replace(settings.entry, entry1_end_time=time(10, 0)))
    extended_data = runner.run(settings, wednesday, monday)
    extended = extended_data["scenarios"][1]
    first, second = extended["trades"]
    assert first["date"] == wednesday.isoformat() and first["exit_time"] == monday_session.open_utc + 1200
    adjustment, = first["stop_adjustments"]
    assert adjustment["prior_session_count"] == 2 and adjustment["effective_time"] == first["exit_time"]
    assert second["date"] == monday.isoformat() and first["exit_time"] < second["entry_time"]
    assert second["stop_adjustments"] == ()
    assert first["equity_after"] == pytest.approx(100000 + first["pnl"])
    assert second["equity_after"] == pytest.approx(first["equity_after"] + second["pnl"])
    assert extended["stats"]["net_pnl"] == pytest.approx(first["pnl"] + second["pnl"])
    assert extended["open_position"] is None
    assert [point["time"] for point in extended["equity"]] == sorted(
        {point["time"] for point in extended["equity"]}
    )
    restarted = runner.run(settings, monday, monday)
    assert restarted["months"]["2025-12"] == extended_data["months"]["2025-12"]
    restarted_case = restarted["scenarios"][1]
    assert restarted_case["equity"][0] == {"time": monday_session.open_utc, "value": 100000}
    assert [trade["date"] for trade in restarted_case["trades"]] == ["2025-12-01"]


def test_exit3_checks_verified_prior_retained_before_publication(runner, replay_files):
    store, daily, config = replay_files
    prior, bars = tape({0: (94, 101, 93, 100)}, day=date(2025, 11, 24))
    with store._connection(write=True) as db:
        db.execute("DELETE FROM metadata WHERE key='verified_contiguous_start'")
    store.record(prior, "complete", bars)
    with store._connection(write=True) as db:
        db.execute("INSERT INTO metadata VALUES ('verified_contiguous_start', '2025-11-25')")
    payload = json.loads(daily.read_bytes())
    payload["bars"].insert(0, {"time": "2025-11-24", "open": 100, "lower": 99})
    daily.write_text(json.dumps(payload), encoding="utf-8")
    settings = replace(runner.load_settings(config), trailing_stop=True, next_session_open=False)
    data = runner.run(settings)
    first = data["months"]["2025-11"][0]
    assert first["prior_lowers"][0] == {
        "date": "2025-11-24", "lower": None, "status": "opening_price_mismatch",
    }
    assert data["coverage"]["cache_start"] == "2025-11-25"
    assert first["bars"][0]["atr"] is None
    assert data["coverage"]["retained_inputs"] == [{
        "date": "2025-11-24", "bars": 78, "roles": ["prior_open_check", "volume_sma_seed"],
        "volume_seed_bars": 19,
    }]
    assert data["coverage"]["fingerprinted_bars"] == 78 + data["coverage"]["signal_bars"]
    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET open=96 WHERE time=?", (bars[0].time,))
    revised = runner.run(settings)
    assert revised["months"]["2025-11"][0]["prior_lowers"][0]["lower"] == 99
    assert revised["fingerprints"]["intraday_sha256"] != data["fingerprints"]["intraday_sha256"]


@pytest.mark.parametrize(("retained_day", "expected_sma"), [
    (date(2025, 11, 24), 95.5),
    (date(2025, 11, 21), None),
])
def test_report_seeds_volume_from_retained_session_without_daily_lookup(runner, replay_files, retained_day, expected_sma):
    store, _, config = replay_files
    prior, bars = tape(day=retained_day)
    with store._connection(write=True) as db:
        db.execute("DELETE FROM metadata WHERE key='verified_contiguous_start'")
    store.record(prior, "complete", [replace(bar, volume=100) for bar in bars])
    with store._connection(write=True) as db:
        db.execute("INSERT INTO metadata VALUES ('verified_contiguous_start', '2025-11-25')")
    settings = replace(runner.load_settings(config), next_session_open=False)
    for trailing in (False, True):
        data = runner.run(replace(settings, trailing_stop=trailing))
        first = data["months"]["2025-11"][0]["bars"]
        assert first[0]["volume_sma_20"] == expected_sma
        assert first[19]["volume_sma_20"] == 10
        assert first[0]["atr"] is None
        retained = data["coverage"]["retained_inputs"]
        if expected_sma is None:
            assert retained == []
        else:
            assert retained == [{
                "date": retained_day.isoformat(), "bars": 78, "roles": ["volume_sma_seed"], "volume_seed_bars": 19,
            }]


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


def test_unverified_optional_tail_does_not_abort_same_day_results(runner, replay_files):
    store, _, config = replay_files
    with store._connection(write=True) as db:
        db.execute("INSERT INTO conflicts SELECT time,date FROM bars WHERE date='2025-12-01' LIMIT 1")
    friday = date(2025, 11, 28)
    result = runner.run(runner.load_settings(config), friday, friday)
    same, overnight = result["scenarios"]
    assert len(same["trades"]) == 1 and overnight["skips"]["next_open_unavailable"] == 1
    assert result["coverage"]["next_open_tail"] is None
    assert "2025-12-01" in result["coverage"]["next_open_tail_unavailable"]


def test_verified_next_open_beyond_published_signal_cutoff_can_close_existing_trade(runner, replay_files):
    store, _, config = replay_files
    with store._connection(write=True) as db:
        db.execute("UPDATE metadata SET value='2025-11-28' WHERE key='target'")
    friday = date(2025, 11, 28)
    result = runner.run(runner.load_settings(config), friday, friday)
    trade, = result["scenarios"][1]["trades"]
    assert (trade["date"], trade["exit_date"]) == ("2025-11-28", "2025-12-01")
    assert result["coverage"]["cache_end"] == "2025-11-28"
    assert result["months"]["2025-12"][0]["exit_only"]


def test_malformed_optional_tail_is_a_verification_failure(runner, replay_files):
    store, _, config = replay_files
    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET open='broken' WHERE time=(SELECT MIN(time) FROM bars WHERE date='2025-12-01')")
    friday = date(2025, 11, 28)
    result = runner.run(runner.load_settings(config), friday, friday)
    assert result["scenarios"][0]["stats"]["trades"] == 1
    assert result["scenarios"][1]["skips"]["next_open_unavailable"] == 1
    assert "2025-12-01" in result["coverage"]["next_open_tail_unavailable"]


def test_runner_does_not_create_sidecars_beside_closed_wal_input(runner, replay_files):
    store, _, config = replay_files
    sidecars = [Path(str(store.path) + suffix) for suffix in ("-wal", "-shm", "-journal")]
    assert not any(p.exists() for p in sidecars)
    original = store.path.read_bytes()
    runner.run(runner.load_settings(config), date(2025, 11, 28), date(2025, 11, 28))
    assert store.path.read_bytes() == original
    assert not any(p.exists() for p in sidecars)


def test_input_snapshot_includes_committed_uncheckpointed_wal(runner, replay_files):
    store, _, config = replay_files
    friday = date(2025, 11, 28)
    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET wap=100.25 WHERE time=(SELECT MIN(time) FROM bars WHERE date=?)",
                   (friday.isoformat(),))
        db.commit()
        wal = Path(str(store.path) + "-wal")
        before = wal.read_bytes()
        assert before
        report = runner.run(runner.load_settings(config), friday, friday)
        assert report["months"]["2025-11"][0]["bars"][0]["vwap"] == 100.25
        assert wal.read_bytes() == before


@pytest.mark.parametrize(("starting_cash", "trade_count"), [("105.07", 3), ("105.069999999999999", 2)])
def test_compounded_cash_remains_exact_at_the_next_order_boundary(runner, replay_files, starting_cash, trade_count):
    store, daily, config = replay_files
    payload = json.loads(daily.read_bytes())
    for row in payload["bars"]:
        row.update(open=98, upper=99, lower=97)
    daily.write_text(json.dumps(payload), encoding="utf-8")
    with config.open("a", encoding="utf-8") as stream:
        stream.write(f"[account]\nstarting_balance={starting_cash}\nrisk_pct=100\nslippage_bps=0\n")
    settings = runner.load_settings(config)
    settings = replace(settings, next_session_open=False,
                       entry=replace(settings.entry, entry_atr_buffer=0))
    first, last = date(2025, 11, 26), date(2025, 12, 1)
    with store._connection(write=True) as db:
        for day, close_reference in [(first, 100.02), (date(2025, 11, 28), 99.99), (last, 100.03)]:
            _, bars = tape({0: (98, 99, 97, 98), 1: (98, 99, 97, 98), 2: (98, 100, 98, 99.5),
                            3: (99.5, 100.01, 98, 99), 4: (100.01, 101, 99, 100.5)}, day=day)
            db.executemany("UPDATE bars SET open=?,high=?,low=?,close=? WHERE time=?",
                           [(b.open, b.high, b.low, b.close, b.time) for b in bars[:-1]])
            db.execute("UPDATE bars SET close=? WHERE time=?", (close_reference, bars[-1].time))
    trades = runner.run(settings, first, last)["scenarios"][0]["trades"]
    assert [trade["pnl"] for trade in trades[:2]] == [-2.01, -2.04]
    assert len(trades) == trade_count
    if trade_count == 3:
        assert [trade["equity_after"] for trade in trades] == [103.06, 101.02, 99.02]
        assert trades[-1]["shares"] == 1 and trades[-1]["entry_price"] == 100.02


def test_hard_linked_input_cannot_hide_wal_updates(runner, replay_files):
    store, _, config = replay_files
    alias = store.path.with_name("alias.db")
    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET wap=100.25 WHERE time=(SELECT MAX(time) FROM bars)")
        db.commit()
        os.link(store.path, alias)
        with pytest.raises(ValueError, match="hard link"):
            runner.run(replace(runner.load_settings(config), intraday_cache=alias))


@pytest.mark.parametrize(("key", "value"), [("ticker", "QQQ"), ("source", "Other"), ("schema", "unknown")])
def test_daily_snapshot_rejects_wrong_identity(runner, replay_files, key, value):
    _, daily, config = replay_files
    snapshot = json.loads(daily.read_bytes())
    snapshot[key] = value
    daily.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        runner.run(runner.load_settings(config))


def test_unknown_unlabelled_daily_snapshot_is_not_assumed_to_be_spy(runner, replay_files):
    _, daily, config = replay_files
    payload = json.loads(daily.read_bytes())
    daily.write_text(json.dumps({"bars": payload["bars"]}), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        runner.run(runner.load_settings(config))


def test_audited_legacy_snapshot_remains_read_only(runner, replay_files, monkeypatch):
    _, daily, config = replay_files
    payload = json.loads(daily.read_bytes())
    raw = json.dumps({"bars": payload["bars"]}).encode()
    daily.write_bytes(raw)
    monkeypatch.setattr(runner, "LEGACY_SPY_DAILY_SHA256", hashlib.sha256(raw).hexdigest(), raising=False)
    assert runner.run(runner.load_settings(config))["fingerprints"]["daily_sha256"] == hashlib.sha256(raw).hexdigest()
    assert daily.read_bytes() == raw


def test_invalid_published_endpoint_is_not_rounded_to_a_trading_day(runner, replay_files):
    store, _, config = replay_files
    with store._connection(write=True) as db:
        db.execute("UPDATE metadata SET value='2025-11-27' WHERE key='target'")
    with pytest.raises(ValueError, match="published.*NYSE|Published.*NYSE"):
        runner.run(runner.load_settings(config))


@pytest.mark.skipif(os.name != "nt", reason="Alternate data streams are a Windows path feature")
def test_windows_stream_names_cannot_write_to_input(runner, replay_files):
    from qqq_viewer.spy_5m_report import write_report

    store, _, config = replay_files
    stream = store.path.with_name(store.path.name + ":report.html")
    settings = runner.load_settings(config)
    with pytest.raises(ValueError, match="alternate data streams"):
        runner.output_path(stream, settings, {})
    with pytest.raises(ValueError, match="alternate data streams"):
        write_report(stream, {})
    assert runner.output_path(store.path.with_name("report.html"), settings, {}).name == "report.html"


def test_bear_close_atr_with_overnight_gap_and_no_future_range(runner, replay_files):
    store, _, config = replay_files
    day = date(2025, 11, 26)
    earlier, warm = tape(day=date(2025, 11, 25))
    session, bars = tape({0: (104, 105, 103, 104), 1: (104, 105, 103, 104),
                          2: (104, 106, 103, 105), 3: (105, 106, 101, 102),
                          4: (106, 108, 101, 107)}, day=day)
    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET open=100,high=101,low=99,close=100 WHERE date=?", (earlier.date.isoformat(),))
        db.executemany("UPDATE bars SET open=?,high=?,low=?,close=? WHERE time=?",
                       [(b.open, b.high, b.low, b.close, b.time) for b in bars])
    config = runner.load_settings(config)
    def levels():
        scoped = runner.run(config, day, day)["scenarios"][0]
        return next((e["trigger"], e["stop"]) for e in scoped["events"] if e["action"] == "submitted")
    # Prior true ranges 2; then 5 (overnight gap), 2, 3, 5 → ATR 2.32610625.
    assert levels() == (106.59, 98.0)
    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET high=120,low=88 WHERE time=?", (bars[4].time,))
    assert levels() == (106.59, 98.0)
