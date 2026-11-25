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
