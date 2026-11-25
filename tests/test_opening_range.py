from dataclasses import replace
from datetime import date

import pytest

from qqq_viewer.opening_range import Entry3Settings, EntrySettings, Settings, WilderATR, commission, replay_session
from qqq_viewer.sessions import Session
from qqq_viewer.store import Bar


SESSION = Session(date(2020, 1, 2), 1577975400, 1577998800)
CFG = Settings()


def day(replacements):
    bars = [Bar(t, 99, 100, 98, 99, 1000) for t in SESSION.minutes]
    for index, (opening, high, low, close) in replacements.items():
        bars[index] = Bar(bars[index].time, opening, high, low, close, 1000)
    return bars


def test_rid_uses_prior_bounds_one_cent_tolerance_and_25_bar_limit():
    from qqq_viewer.rid import calculate_rid

    bars = day({26: (99, 101, 98, 99.99)})
    rid = calculate_rid(bars[:27])
    assert rid.prior_upper == (None,) + (100,) * 26
    assert rid.prior_length == (*range(26), 1)
    assert [(s.first, s.last, s.upper, s.lower) for s in rid.segments] == [
        (bars[0].time, bars[24].time, 100, 98),
        (bars[25].time, bars[25].time, 100, 98),
        (bars[26].time, bars[26].time, 101, 98),
    ]
    assert calculate_rid(bars[26:27]).prior_upper == (None,)


@pytest.mark.parametrize(("high", "low", "close", "count"), [
    (200.02, 199, 200.01, 2),
    (201, 200.01, 200.02, 2),
    (200.02, 199, 200.009, 1),
])
def test_rid_one_cent_boundaries(high, low, close, count):
    from qqq_viewer.rid import calculate_rid

    bars = [Bar(0, low, high, low, low, 1), Bar(60, low, high, low, close, 1)]
    assert len(calculate_rid(bars).segments) == count


def replay_entry3(changes, *, entry=None, vwap=110, vwaps=None, missing_atr=None, cfg=None,
                  version="revised"):
    bars = day(changes)
    cfg = cfg or Settings(entry1=EntrySettings(enabled=False), entry3=entry or Entry3Settings())
    atr = [2.] * len(bars)
    if missing_atr is not None:
        atr[missing_atr] = None
    result = replay_session(SESSION, bars, atr, 100_000, cfg, None, 1.,
                            vwaps=[vwap] * len(bars) if vwaps is None else vwaps,
                            rule_version=version)
    return bars, result


@pytest.mark.parametrize("version", ["legacy", "revised"])
@pytest.mark.parametrize(("entry", "levels"), [
    (Entry3Settings(), (105.5, 94.5)),
    (Entry3Settings(entry_atr=.5, stop_atr=.75, stop_lookback=1), (106, 97.5)),
])
def test_entry3_waits_through_doji_and_repeated_breakout(version, entry, levels):
    bars, result = replay_entry3({
        4: (99, 103, 98, 102), 5: (100, 101, 95, 100),
        6: (100, 105, 99, 104), 7: (104, 104, 99, 101),
        8: (101, 108, 99, 105),
    }, entry=entry, version=version)
    order, fill, *rest = result.events
    assert (order.entry_type, order.action, order.signal_time, order.time) == (
        "entry3", "placed", bars[7].time, bars[8].time,
    )
    assert (order.entry_trigger, order.initial_stop) == levels
    assert (fill.action, fill.time) == ("filled", bars[8].time)
    assert result.trades[0].entry_type == "entry3"


def test_entry3_stays_armed_after_doji_inside_old_range():
    bars, result = replay_entry3({
        4: (99, 103, 98, 102),
        5: (99, 100, 98.5, 99),
        6: (100, 101, 98, 99),
    })
    assert [(event.entry_type, event.signal_time, event.time)
            for event in result.events if event.action == "placed"] == [
        ("entry3", bars[6].time, bars[7].time),
    ]


@pytest.mark.parametrize(("reset_at", "minimum", "placed"), [
    (3, 3, False), (2, 3, True), (2, 4, False), (3, 2, True),
])
def test_entry3_requires_minimum_rid_length_before_breakout(reset_at, minimum, placed):
    _, result = replay_entry3({
        reset_at: (95, 100, 94, 95),
        5: (99, 103, 98, 102),
        6: (102, 103, 98, 99),
    }, entry=Entry3Settings(min_rid_bars=minimum))
    assert any(event.action == "placed" for event in result.events) is placed


@pytest.mark.parametrize(("minute", "breakout", "vwap"), [
    (3, (99, 103, 98, 102), 110),
    (4, (99, 103, 98, 100), 110),
    (4, (99, 103, 98, 99.5), 110),
    (4, (102, 103, 98, 101), 110),
    (4, (99, 103, 98, 102), 103),
    (4, (99, 103, 98, 102), None),
    (25, (98.5, 103, 98, 99), 110),
])
def test_entry3_rejects_nonqualifying_breakout(minute, breakout, vwap):
    _, result = replay_entry3({minute: breakout, minute + 1: (100, 101, 98, 99)}, vwap=vwap)
    assert not result.events


def test_entry3_consumes_warmup_signal_and_starts_fresh_each_replay():
    bear = (100, 101, 98, 99)
    _, result = replay_entry3({4: (99, 103, 98, 102), 5: bear, 6: bear}, missing_atr=5)
    assert [(event.action, event.reason) for event in result.events] == [("skipped", "warmup")]
    _, result = replay_entry3({149: (99, 103, 98, 102)})
    assert not result.events
    _, result = replay_entry3({4: bear})
    assert not result.events


def test_entry3_no_active_window_after_1159_signal():
    bars, result = replay_entry3({148: (99, 103, 98, 102), 149: (102, 103, 98, 99)})
    assert [(event.signal_time, event.reason) for event in result.events] == [
        (bars[149].time, "no_active_window"),
    ]


def test_entry3_signal_loses_to_entry2_and_is_consumed():
    cfg = Settings(entry1=EntrySettings(enabled=False), entry2=EntrySettings(), entry3=Entry3Settings())
    _, result = replay_entry3({
        4: (99, 103, 98, 102), 5: (99, 101, 98, 100), 6: (99, 101, 98, 100),
        7: (99, 101, 98, 100), 8: (100, 101, 98, 99), 9: (100, 101, 98, 99),
    }, cfg=cfg, version="legacy")
    assert [(event.entry_type, event.action) for event in result.events] == [
        ("entry2", "placed"), ("entry2", "canceled"),
    ]


def test_entry3_signal_during_open_position_is_not_queued():
    cfg = Settings(entry3=Entry3Settings(), trail_lookback=0)
    _, result = replay_entry3({
        15: (101, 103, 101, 102), 16: (102, 104, 101, 102),
        17: (102, 105, 101, 104), 18: (104, 105, 101, 102),
        19: (98, 99, 96, 98), 20: (100, 101, 98, 99),
    }, cfg=cfg)
    assert [(event.entry_type, event.action) for event in result.events] == [
        ("entry1", "placed"), ("entry1", "filled"),
    ]
    assert result.trades[0].exit_reason == "stop"


def test_wilder_atr_uses_prior_close_and_seeds_arithmetic_mean():
    atr = WilderATR(2)
    assert atr.update(Bar(0, 100, 101, 99, 100, 1)) is None
    assert atr.update(Bar(60, 105, 106, 104, 105, 1)) == 4
    assert atr.update(Bar(120, 105, 106, 104, 105, 1)) == 3


def test_entry_stop_active_after_signal_bar():
    bars = day({15: (101, 110, 101, 105), 17: (102, 106, 101, 104), 18: (105, 109, 101, 104)})
    result = replay_session(SESSION, bars, [2.0] * len(bars), 100_000, CFG, None, 1.0)
    assert result.events[0].signal_time == bars[15].time
    assert result.events[-1].action == "canceled"
    assert not result.trades
    assert result.events[0].initial_stop == 97.5


def test_ambiguous_same_minute_uses_adverse_path_and_counts_costs():
    bars = day({15: (101, 103, 101, 102), 16: (102, 105, 96, 102)})
    result = replay_session(SESSION, bars, [2.0] * len(bars), 100_000, CFG, 1.5, 1.0)
    trade = result.trades[0]
    assert trade.entry_time == trade.exit_time == bars[16].time
    assert trade.ambiguous
    assert trade.shares == 41
    assert result.events[0].initial_stop == trade.initial_stop == 97.5
    assert trade.entry_price == pytest.approx(103.51035)
    assert trade.exit_price == pytest.approx(97.49)
    assert trade.pnl == pytest.approx((trade.exit_price - trade.entry_price) * 41 - 2)
    assert trade.mfe_pct == 0


def test_noon_cancellation_and_forced_exit_at_one():
    late = day({149: (101, 102, 101, 102), 150: (103, 104, 103, 104)})
    assert replay_session(SESSION, late, [2.0] * len(late), 100_000, CFG, None, 1.0).events[0].reason == "no_active_window"
    bars = day({15: (101, 103, 101, 102), **{i: (104, 105, 103, 104) for i in range(16, 210)},
                210: (106, 108, 90, 92)})
    trade = replay_session(SESSION, bars, [2.0] * len(bars), 100_000, CFG, None, 1.0).trades[0]
    assert trade.exit_time == bars[210].time
    assert trade.exit_reason == "time"
    assert trade.exit_price == pytest.approx(105.9894)


def test_commission_minimum_variable_fee_and_order_cap():
    assert commission(10, 100) == 1
    assert commission(300, 100) == 1.50
    assert commission(10, 0.2) == 0.02


def test_vwap_trail_activates_next_minute_and_fills_at_gap_open():
    replacements = {
        15: (101, 103, 101, 102), 16: (104, 105, 103, 104),
        **{i: (130, 131, 129, 130) for i in range(17, 26)},
        26: (106, 106, 104, 104), 27: (103, 104, 102, 103),
    }
    bars = day(replacements)
    trade = replay_session(SESSION, bars, [2.0] * len(bars), 100_000, CFG, None, 1.0).trades[0]
    assert trade.stop_updates == [(bars[27].time, 103.5)]
    assert trade.exit_time == bars[27].time
    assert trade.exit_price == pytest.approx(102.9897)
    assert trade.gap_slippage == pytest.approx((0.5 + 0.5) * trade.shares)


def replay(bars, **kwargs):
    from qqq_viewer.opening_range import EntrySettings, replay_session

    cfg = Settings(entry1=EntrySettings(enabled=kwargs.pop("entry1", True)),
                   entry2=EntrySettings(enabled=kwargs.pop("entry2", True), risk_pct=.5),
                   **kwargs.pop("settings", {}))
    return replay_session(kwargs.pop("session", SESSION), bars, [2.] * len(bars),
                          kwargs.pop("equity", 100_000), cfg, None, 1.)


@pytest.mark.parametrize("count", [4, 6])
def test_entry2_first_bear_after_consecutive_bull_bars(count):
    bars = day({**{i: (99, 100, 98, 99.5) for i in range(count)}, count: (100, 102, 99, 99.5)})
    event = replay(bars, entry1=False).events[0]
    assert (event.entry_type, event.signal_time, event.time) == ("entry2", bars[count].time, bars[count + 1].time)
    assert (event.entry_trigger, event.initial_stop) == (102.5, 97.5)


def test_doji_breaks_entry2_streak():
    bars = day({0: (99, 100, 98, 99.5), 1: (99, 100, 98, 99.5), 3: (99, 100, 98, 99.5),
                4: (100, 102, 99, 99.5)})
    assert replay(bars, entry1=False).events == []


@pytest.mark.parametrize(("close", "previous_vwap", "signal_vwap", "broken_streak", "placed"), [
    (99.5, 101, 101, False, True),
    (100, 99, 103, False, True),
    (100, 100, 102, False, False),
    (99.5, None, None, False, False),
    (99.5, None, 101, False, True),
    (100, 99, None, False, True),
    (99.5, 99, 101, True, False),
])
def test_revised_entry2_uses_each_candles_own_vwap(close, previous_vwap, signal_vwap, broken_streak, placed):
    changes = {i: (99, 100, 98, 99.5) for i in range(4)}
    if broken_streak:
        changes[1] = (99, 100, 98, 99)
    bars = day(changes | {4: (100, 102, 99, close)})
    vwap = [None] * len(bars)
    vwap[3:5] = [previous_vwap, signal_vwap]
    cfg = replace(CFG, entry1=EntrySettings(enabled=False), entry2=EntrySettings())
    result = replay_session(SESSION, bars, [2.] * len(bars), 100_000, cfg, None, 1.,
                            vwaps=vwap, rule_version="revised")
    assert [(e.action, e.entry_type, e.signal_time) for e in result.events if e.action == "placed"] == (
        [("placed", "entry2", bars[4].time)] if placed else []
    )


def test_revised_trail_waits_for_bull_and_rearms():
    bars = day({
        15: (101, 103, 101, 102), 16: (104, 105, 103, 104),
        **{i: (108, 109, 106, 108) for i in range(17, 32)},
        20: (109, 110, 106, 108), 22: (109, 110, 107, 108),
        24: (106, 109, 104, 108), 25: (109, 110, 107, 109),
        26: (110, 111, 108, 109), 27: (109, 111, 108, 110),
        28: (109, 111, 108, 110), 29: (109, 111, 108, 110),
        30: (110, 111, 108, 109), 31: (109, 111, 108, 110),
        32: (107, 109, 106, 108),
    })
    atrs = [2.] * len(bars)
    atrs[24] = 4.
    result = replay_session(SESSION, bars, atrs, 100_000, CFG, None, 1.,
                            vwaps=[130.] * len(bars), rule_version="revised")
    trade = result.trades[0]
    assert trade.stop_updates == [(bars[25].time, 105.), (bars[32].time, 107.5)]
    assert trade.exit_time == bars[32].time


def test_revised_trail_pending_state_ends_with_position():
    bars = day({
        15: (101, 103, 101, 102), 16: (104, 105, 103, 104),
        20: (105, 106, 101, 103), 21: (97, 98, 95, 96),
        25: (101, 103, 101, 102), 26: (104, 106, 103, 105),
    })
    result = replay_session(SESSION, bars, [2.] * len(bars), 100_000, CFG, None, 1.,
                            vwaps=[130.] * len(bars), rule_version="revised")
    assert [trade.entry_time for trade in result.trades] == [bars[16].time, bars[26].time]
    assert [trade.stop_updates for trade in result.trades] == [[], []]


def test_revised_zero_trail_lookback_leaves_stop_unchanged():
    bars = day({15: (101, 103, 101, 102), 16: (104, 105, 103, 104),
                17: (105, 106, 103, 104), 18: (104, 106, 103, 105)})
    result = replay_session(SESSION, bars, [2.] * len(bars), 100_000,
                            replace(CFG, trail_lookback=0), None, 1.,
                            vwaps=[130.] * len(bars), rule_version="revised")
    assert result.trades[0].stop_updates == []


def test_cross_entry_replacement_resizes_and_earlier_fill_has_precedence():
    base = {15: (101, 110, 101, 105), **{i: (100, 104, 100, 103) for i in range(17, 21)}}
    bars = day({**base, 21: (103, 104, 101, 102), 22: (104, 105, 101, 102)})
    result = replay(bars)
    old, replacement, fill = result.events[:3]
    assert (old.action, replacement.action, fill.action) == ("placed", "replaced", "filled")
    assert replacement.prior_order_id == old.order_id
    assert (replacement.time, replacement.entry_type, replacement.entry_trigger,
            replacement.initial_stop, replacement.shares) == (bars[22].time, "entry2", 104.5, 99.5, 100)
    assert result.trades[0].order_id == replacement.order_id
    reached_old = replay(day({**base, 21: (103, 111, 101, 102)}))
    assert reached_old.trades[0].order_id == 1
    assert [e.action for e in reached_old.events] == ["placed", "filled"]


def test_same_entry_strict_replacement_and_noon_expiration():
    bars = day({15: (101, 110, 101, 105), 16: (101, 110, 101, 105), 21: (101, 106, 101, 105),
                22: (101, 106.3, 101, 105), 149: (101, 106, 101, 105),
                150: (107, 109, 106, 108)})
    result = replay(bars, entry2=False)
    assert not result.trades
    assert [e.action for e in result.events] == ["placed", "skipped", "replaced", "skipped", "skipped", "canceled"]
    assert result.events[-2].reason == "no_active_window"
    assert result.events[-1].time == bars[150].time


def test_sequential_fills_use_realized_balance_and_early_close_precedence():
    bars = day({15: (101, 103, 101, 102), 16: (102, 105, 96, 102),
                17: (101, 105, 101, 104), 18: (104, 106, 103, 105)})
    short = Session(SESSION.date, SESSION.open_utc, SESSION.open_utc + 210 * 60)
    first, second = replay(bars[:210], session=short, entry2=False).trades
    assert first.ambiguous and second.entry_time > first.exit_time
    assert second.exit_reason == "early_close" and second.exit_time == short.close_utc
    assert second.shares == int((100_000 + first.pnl) * .0025 / (105.5 - 95.5))
    bars[209] = Bar(bars[209].time, 99, 102, 90, 99, 1000)
    assert replay(bars[:210], session=short, entry2=False).trades[-1].exit_time == bars[209].time


def test_gap_with_zero_affordable_shares_clears_order():
    from qqq_viewer.opening_range import EntrySettings, replay_session

    bars = day({15: (101, 103, 101, 102), 16: (120, 122, 119, 121),
                17: (101, 103, 101, 102)})
    cfg = Settings(entry1=EntrySettings(entry_lookback=0, stop_lookback=0, risk_pct=100), leverage=1.1)
    result = replay_session(SESSION, bars, [2.] * len(bars), 100., cfg, None, 1.)
    assert [e.action for e in result.events] == ["placed", "skipped", "skipped", "placed", "canceled"]
    assert result.events[1].reason == "fill_buying_power"
    assert result.events[2].signal_time == bars[16].time


def test_entry_risk_floors_exact_tick_distance():
    bars = [Bar(t, 229.70, 229.79, 229.67, 229.70, 1000) for t in SESSION.minutes]
    bars[15] = Bar(bars[15].time, 229.85, 229.97, 229.80, 229.90, 1000)
    result = replay_session(SESSION, bars, [.2] * len(bars), 100_000, CFG, None, 1)
    order = result.events[0]
    assert (order.entry_trigger, order.initial_stop, order.shares) == (230.02, 229.62, 625)


@pytest.mark.parametrize("multiplier", [1, 2])
def test_nonpositive_modeled_exit_is_rejected(multiplier):
    from dataclasses import replace

    bars = day({15: (101, 103, 101, 102), 16: (102, 105, 96, 102)})
    cfg = replace(CFG, slippage_floor=100 / multiplier)
    with pytest.raises(ValueError, match="nonpositive.*exit"):
        replay_session(SESSION, bars, [2.] * len(bars), 100_000, cfg, None, multiplier)


@pytest.mark.parametrize("trigger_index", [149, 150])
def test_entry2_existing_order_can_fill_at_1159_but_not_noon(trigger_index):
    bars = day({**{i: (99, 100, 98, 99.5) for i in range(140, 144)},
                144: (100, 102, 99, 99.5), trigger_index: (102, 104, 101, 103)})
    result = replay(bars, entry1=False)
    if trigger_index == 149:
        assert result.trades[0].entry_time == bars[149].time
        assert result.events[1].action == "filled"
    else:
        assert not result.trades
        assert result.events[1].action == "canceled"
        assert result.events[1].time == bars[150].time


def test_entry2_1159_signal_has_no_active_window():
    bars = day({**{i: (99, 100, 98, 99.5) for i in range(145, 149)},
                149: (100, 102, 99, 99.5), 150: (105, 106, 104, 105)})
    result = replay(bars, entry1=False)
    assert not result.trades
    assert [(e.action, e.entry_type, e.reason, e.time) for e in result.events] == [
        ("skipped", "entry2", "no_active_window", bars[150].time),
    ]
