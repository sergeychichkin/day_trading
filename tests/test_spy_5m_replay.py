from dataclasses import replace
from datetime import date, time

import pytest

from qqq_viewer.spy_5m_store import FiveMinuteBar, expected_times, sessions


@pytest.fixture
def replay():
    from qqq_viewer import spy_5m_replay
    return spy_5m_replay


def tape(overrides=None, day=date(2025, 11, 26)):
    session, = sessions(day, day)
    values = {0: (100, 101, 98, 100), 1: (100, 101, 99, 99),
              2: (100, 102, 99, 101.5), 3: (101.5, 102, 100, 101)}
    values.update(overrides or {})
    bars = [FiveMinuteBar(t, *values.get(i, (100, 101, 99, 100)), 10, 100)
            for i, t in enumerate(expected_times(session))]
    return session, bars


def simulate(replay, overrides=None, *, entry=None, account=None, mode="same_day_close",
             day=date(2025, 11, 26), next_open=None):
    session, bars = tape(overrides, day)
    return replay.replay_day(session, bars, [2] * len(bars), 101, 100000,
                             entry or replay.EntrySettings(), account or replay.AccountSettings(), mode,
                             next_open=next_open)


def test_first_bull_and_first_later_bear_own_the_only_setup(replay):
    session, _ = tape()
    result = simulate(replay, {0: (100, 101, 98, 101), 4: (100, 102, 97, 100),
                              5: (101, 103, 98, 102)})
    trade = result.trade
    assert (trade.signal_time, trade.bear_time) == (session.open_utc + 600, session.open_utc + 900)
    assert (trade.entry_trigger, trade.initial_stop, trade.shares) == (102.5, 97.5, 200)
    assert simulate(replay, {2: (100, 101, 99, 100), 3: (100, 103, 99, 102)}).trade is None


def test_order_activates_on_next_bar_and_expires_exclusive(replay):
    session, _ = tape()
    at_expiry = 4 + 24
    assert simulate(replay, {at_expiry: (100, 110, 99, 105)}).trade is None
    trade = simulate(replay, {at_expiry - 1: (100, 110, 99, 105)}).trade
    assert trade.entry_time == session.open_utc + (at_expiry - 1) * 300
    later_signal = {5: (100, 104, 98, 103), 6: (103, 104, 98, 100), 7: (100, 110, 98, 109)}
    assert simulate(replay, later_signal, entry=replace(replay.EntrySettings(),
                    entry1_end_time=time(10, 30), entry_order_valid_minutes=5)).trade is None
    early_day = date(2025, 11, 28)
    early, _ = tape(day=early_day)
    result = simulate(replay, {3: (100, 101, 99, 100), 41: (101, 105, 99, 100)}, day=early_day)
    assert result.trade is None
    assert not any(event.action == "activated" for event in result.events)
    assert early.close_utc - early.open_utc == 42 * 300


def test_size_and_costs_use_only_submission_inputs(replay):
    trade = simulate(replay, {4: (103, 105, 101, 104)}).trade
    assert trade.shares == 200 and trade.entry_price == pytest.approx(103.0103)
    assert trade.commission == 2 and trade.gap_slippage == pytest.approx(100)
    tight = {0: (104.7, 105, 104.6, 104.7), 1: (104.7, 105, 104.6, 104.7),
             2: (104.7, 105.5, 104.7, 105.25), 3: (105.2, 105.5, 104.8, 105),
             4: (105.5, 106.1, 104.5, 105.8)}
    no_buffers = replace(replay.EntrySettings(), entry_atr_buffer=0, stop_atr_buffer=0)
    affordable = simulate(replay, tight, entry=no_buffers).trade
    assert affordable.shares == 947
    tight[4] = (120, 121, 119, 120)
    rejected = simulate(replay, tight, entry=no_buffers)
    assert rejected.trade is None and any(event.action == "rejected" for event in rejected.events)


def test_stop_at_open_and_ambiguous_entry_bar_have_distinct_fills(replay):
    inside = simulate(replay, {4: (101, 105, 97, 104)}).trade
    assert inside.ambiguous and inside.exit_price == pytest.approx(97.49)
    opening = simulate(replay, {4: (103, 105, 97, 104)}).trade
    assert not opening.ambiguous and opening.exit_price == pytest.approx(97.49)
    stopped = simulate(replay, {4: (103, 105, 102, 104), 5: (95, 100, 94, 98)}).trade
    assert stopped.exit_price == pytest.approx(94.99)
    assert stopped.gap_slippage == pytest.approx(600)
    early, _ = tape(day=date(2025, 11, 28))
    same = simulate(replay, {4: (103, 105, 102, 104)}, day=early.date).trade
    assert same.exit_time == early.close_utc
    protected = simulate(replay, {4: (103, 105, 102, 104), 41: (100, 105, 96, 104)}, day=early.date).trade
    assert protected.exit_reason == "protective_stop" and protected.exit_time < early.close_utc
    next_session, _ = tape(day=date(2025, 12, 1))
    overnight = simulate(replay, {4: (103, 105, 102, 104)}, day=early.date,
                         mode="next_session_open", next_open=(next_session.open_utc, 95)).trade
    assert (overnight.exit_time, overnight.exit_reason) == (next_session.open_utc, "next_session_open")
    assert overnight.exit_price == pytest.approx(94.99)
    boundary = simulate(replay, {4: (103, 105, 102, 104)}, mode="next_session_open")
    assert boundary.trade is None and boundary.skip_reason == "next_open_unavailable"


def test_exact_cash_boundary_is_affordable_at_submission_and_fill(replay):
    day, bars = tape({0: (98, 99, 97, 98), 1: (98, 99, 97, 98), 2: (98, 100, 98, 99.5),
                      3: (99.5, 100.01, 98, 99), 4: (100.01, 101, 99, 100.5)})
    entry = replace(replay.EntrySettings(), entry_atr_buffer=0, stop_atr_buffer=0)
    account = replace(replay.AccountSettings(), risk_pct=100, slippage_bps=0)
    result = replay.replay_day(day, bars, [2] * len(bars), 99, 101.02, entry, account, "same_day_close")
    assert result.trade.shares == 1 and result.trade.entry_price == 100.02


@pytest.mark.parametrize(("close", "ambiguous"), [(97, False), (97.5, False), (98, True)])
def test_ambiguity_requires_a_close_above_the_stop(replay, close, ambiguous):
    trade = simulate(replay, {4: (101, 105, 96, close)}).trade
    assert trade.exit_price == pytest.approx(97.49)
    assert trade.ambiguous is ambiguous


@pytest.mark.parametrize("outcome", ["stop", "rejection"])
def test_no_rearm_after_a_stop_or_gap_rejection(replay, outcome):
    later = {5: (100, 104, 98, 103), 6: (103, 104, 98, 100), 7: (105, 110, 102, 109)}
    fill = (103, 105, 96, 101) if outcome == "stop" else (800, 801, 799, 800)
    result = simulate(replay, {4: fill, **later}, entry=replace(replay.EntrySettings(), entry1_end_time=time(10, 30)))
    assert sum(event.action == "submitted" for event in result.events) == 1
    assert bool(result.trade) == (outcome == "stop")


def trail(replay, overrides, *, day=date(2025, 11, 28), atr=1.01, lowers=(105, 107),
          next_bar_open=None, position=None, upper=101):
    session, bars = tape(overrides, day)
    return replay.replay_day(
        session, bars, [atr] * len(bars), upper, 100000,
        replace(replay.EntrySettings(), entry_atr_buffer=0, stop_atr_buffer=0),
        replace(replay.AccountSettings(), slippage_bps=0, slippage_floor=0),
        "trailing_stop", position=position, prior_lowers=lowers, next_bar_open=next_bar_open,
    )


def test_trailing_stop_starts_next_bar_and_only_rises(replay):
    result = trail(replay, {
        4: (103, 105, 99, 103),
        5: (104, 106, 104, 105),
        6: (104, 107, 104, 106),  # Equal to the higher level.
        7: (105, 106, 105, 105),  # Doji.
        8: (104.1, 105, 103.6, 104.5),  # Lower candidate.
        9: (104, 105, 103, 104),
    })
    trade, = result.completed_trades
    adjustment, = trade.stop_adjustments
    assert adjustment.effective_time == adjustment.candle_time + 300
    assert (adjustment.old_stop, adjustment.new_stop) == (98, 103.49)
    assert trade.exit_price == 103.49
    assert trade.exit_time == adjustment.candle_time + 4 * 300


def test_closing_bar_adjustment_survives_weekend(replay):
    monday = date(2025, 12, 1)
    session, _ = tape(day=monday)
    friday = trail(replay, {4: (103, 105, 99, 103), 41: (104, 106, 104, 105)},
                   next_bar_open=session.open_utc)
    assert friday.position.active_stop == 98
    assert friday.position.pending_stop == 103.49
    assert friday.completed_trades == ()
    result = trail(replay, {0: (97, 105, 96, 104), 1: (104, 105, 99, 101),
                            2: (106, 108, 100, 107), 3: (107, 108, 95, 106)},
                   day=monday, position=friday.position)
    first, second = result.completed_trades
    assert first.exit_time == session.open_utc and first.exit_price == 97
    assert first.gap_slippage == pytest.approx((103 - 102 + 103.49 - 97) * first.shares)
    assert first.mae_r == pytest.approx(6 / 5)
    assert second.signal_time == session.open_utc
    assert result.position is None
    assert sum(event.action == "submitted" for event in result.events) == 1
    carried = trail(replay, {0: (100, 101, 99, 100)}, lowers=(None, None), day=monday, position=replace(
        friday.position, pending_stop=None))
    assert carried.position is not None
    assert not any(event.action == "signal" for event in carried.events)
    no_atr = trail(replay, {0: (104, 106, 104, 105)}, atr=None, day=monday,
                   position=replace(friday.position, pending_stop=None))
    assert no_atr.position.stop_adjustments == friday.position.stop_adjustments


@pytest.mark.parametrize(("low", "mode", "expected"), [
    (99, "same_day_close", (2, 0)),
    (97, "same_day_close", (0, 1)),
    (99, "next_session_open", (2, 1.5)),
])
def test_excursions_use_only_execution_interval(replay, low, mode, expected):
    session, bars = tape({
        0: (99, 100, 98, 99), 1: (99, 100, 98, 99), 2: (99, 100, 98, 99.5),
        3: (99.5, 100, 98, 99), 4: (99, 104, low, 101),
        **{index: (101, 102, 100, 101) for index in range(5, 78)},
    })
    entry = replace(replay.EntrySettings(), entry_atr_buffer=0, stop_atr_buffer=0)
    account = replace(replay.AccountSettings(), slippage_floor=0, slippage_bps=0)
    result = replay.replay_day(session, bars, [2] * len(bars), 99, 100000, entry, account,
                               mode, next_open=(session.close_utc + 60000, 97))
    assert (result.trade.mfe_r, result.trade.mae_r) == expected
