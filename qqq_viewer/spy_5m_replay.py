"""One-way Entry 1 replay using only data observable at each decision."""

import math
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from .opening_range import commission, order_price
from .sessions import NEW_YORK, Session
from .spy_5m_store import STEP, FiveMinuteBar, sessions

ExitMode = Literal["same_day_close", "next_session_open", "trailing_stop"]


@dataclass(frozen=True)
class EntrySettings:
    entry1_start_time: time = time(9, 30)
    entry1_end_time: time = time(9, 40)
    entry_atr_buffer: float = 0.25
    entry_order_valid_minutes: int = 120


@dataclass(frozen=True)
class AccountSettings:
    starting_balance: float | Decimal = 100_000
    risk_pct: float | Decimal = 1
    cash_cap: float | Decimal = 1
    tick_size: float = 0.01
    slippage_bps: float | Decimal = 1
    slippage_floor: float | Decimal = 0.01


@dataclass(frozen=True)
class StopAdjustment:
    candle_time: int
    effective_time: int
    old_stop: float
    new_stop: float
    prior_session_count: int


@dataclass(frozen=True)
class OrderEvent:
    time: int
    action: str
    detail: str
    trigger: float | None = None
    stop: float | None = None
    shares: int | None = None
    adjustment: StopAdjustment | None = None


@dataclass
class OpenPosition:
    signal_time: int
    bear_time: int
    entry_time: int
    entry_trigger: float
    initial_stop: float
    shares: int
    entry_price: Decimal
    entry_reference: float
    active_stop: float
    high: Decimal
    low: Decimal
    pending_stop: float | None = None
    stop_adjustments: tuple[StopAdjustment, ...] = ()

    def observe(self, *prices: float | Decimal) -> None:
        values = [_decimal(price) for price in prices]
        self.high = max(self.high, *values)
        self.low = min(self.low, *values)


@dataclass(frozen=True)
class Trade:
    signal_time: int
    bear_time: int
    entry_time: int
    exit_time: int
    entry_trigger: float
    initial_stop: float
    entry_price: float
    exit_price: float
    shares: int
    pnl: Decimal
    commission: float
    slippage: float
    gap_slippage: float
    exit_reason: str
    ambiguous: bool
    mfe_r: float
    mae_r: float
    stop_adjustments: tuple[StopAdjustment, ...]


@dataclass(frozen=True)
class DayResult:
    trade: Trade | None
    events: list[OrderEvent]
    skip_reason: str | None = None
    position: OpenPosition | None = None
    later_trade: Trade | None = None

    @property
    def completed_trades(self) -> tuple[Trade, ...]:
        return tuple(trade for trade in (self.trade, self.later_trade) if trade is not None)


def _decimal(value) -> Decimal:
    return Decimal(str(value))


def _slip(reference: float, account: AccountSettings) -> Decimal:
    return max(_decimal(account.slippage_floor), _decimal(reference) * _decimal(account.slippage_bps) / 10_000)


def _affordable(shares: int, price: Decimal, budget: Decimal) -> bool:
    return price * shares + _decimal(commission(shares, price)) <= budget


def _size(trigger: float, stop: float, equity: float | Decimal, account: AccountSettings) -> tuple[int, Decimal]:
    money = _decimal(equity)
    budget = money * _decimal(account.cash_cap)
    risk = money * _decimal(account.risk_pct) / 100
    risk_limit = int(risk // (_decimal(trigger) - _decimal(stop)))
    expense = _decimal(trigger) + _slip(trigger, account)
    lo, hi = 0, min(risk_limit, int(budget // expense))
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _affordable(mid, expense, budget):
            lo = mid
        else:
            hi = mid - 1
    return lo, budget


def replay_day(session: Session, bars: list[FiveMinuteBar], atrs: list[float | None],
               upper: float | None, equity: float | Decimal, entry: EntrySettings, account: AccountSettings,
               exit_mode: ExitMode, *, session_lower: float | None, next_open: tuple[int, float] | None = None,
               position: OpenPosition | None = None,
               prior_lowers: tuple[float | None, float | None] = (None, None),
               previous_session_date: date | None = None,
               next_bar_open: int | None = None) -> DayResult:
    events: list[OrderEvent] = []
    if exit_mode not in {"same_day_close", "next_session_open", "trailing_stop"}:
        raise ValueError(f"Unknown SPY exit mode: {exit_mode}")
    if len(atrs) != len(bars):
        raise ValueError("ATR and SPY bars must be aligned.")
    if exit_mode == "next_session_open" and next_open is None:
        return DayResult(None, events, "next_open_unavailable")
    if position is not None and exit_mode != "trailing_stop":
        raise ValueError("Only Exit 3 carries an open position.")

    carried_trade = None
    first_signal_time = session.open_utc
    if position is not None:
        position = replace(position)
        if previous_session_date is None:
            prior_sessions = sessions(session.date - timedelta(days=14), session.date - timedelta(days=1))
            previous_session_date = prior_sessions[-1].date if prior_sessions else None
        entry_date = datetime.fromtimestamp(position.entry_time, NEW_YORK).date()
        prior_session_count = 1 if entry_date == previous_session_date else 2
        level = max((value for value in prior_lowers[:prior_session_count] if value is not None), default=None)
        carried_trade = _advance(position, session, bars, atrs, 0, account, exit_mode,
                                 next_open, next_bar_open, level, prior_session_count, events)
        if carried_trade is None:
            return DayResult(None, events, position=position)
        first_signal_time = carried_trade.exit_time
        equity = _decimal(equity) + carried_trade.pnl

    def result(trade=None, reason=None, open_position=None):
        return DayResult(carried_trade or trade, events, reason, open_position,
                         trade if carried_trade is not None else None)

    if upper is None:
        return result(reason="daily_upper_unavailable")
    if equity <= 0:
        return result(reason="cash_unavailable")

    signal = None
    for index, (bar, atr) in enumerate(zip(bars, atrs, strict=True)):
        if bar.time < first_signal_time:
            continue
        if signal is None:
            local = datetime.fromtimestamp(bar.time, NEW_YORK).time()
            if entry.entry1_start_time <= local <= entry.entry1_end_time and bar.close > max(bar.open, upper):
                signal = bar.time
                events.append(OrderEvent(bar.time + STEP, "signal", "Bull closed above the daily upper level"))
            continue
        if bar.close >= bar.open:
            continue

        activate = bar.time + STEP
        if activate >= session.close_utc:
            events.append(OrderEvent(activate, "rejected", "First bear closed at the session close"))
            return result(reason="no_execution_window")
        if atr is None or not math.isfinite(atr) or atr < 0:
            events.append(OrderEvent(activate, "rejected", "ATR(20) not available at the bear close"))
            return result(reason="atr_unavailable")
        trigger = order_price(bar.high + entry.entry_atr_buffer * atr, account.tick_size, up=True)
        if session_lower is None:
            events.append(OrderEvent(activate, "rejected", "Entry session daily Lower is unavailable", trigger))
            return result(reason="daily_lower_unavailable")
        stop = order_price(session_lower, account.tick_size, up=False) if math.isfinite(session_lower) else None
        if stop is None or not 0 < stop < trigger:
            events.append(OrderEvent(activate, "rejected", "Daily Lower must round to a positive stop below the trigger",
                                     trigger, stop))
            return result(reason="invalid_stop")
        shares, budget = _size(trigger, stop, equity, account)
        if not shares:
            events.append(OrderEvent(activate, "rejected", "Position rounds to zero shares", trigger, stop, shares))
            return result(reason="cash_unavailable")
        expiry = min(activate + entry.entry_order_valid_minutes * 60, session.close_utc)
        events.append(OrderEvent(activate, "submitted", "Buy stop and fixed protective stop", trigger, stop, shares))
        events.append(OrderEvent(activate, "activated", "Eligible from this bar's open", trigger, stop, shares))
        for offset in range(index + 1, len(bars)):
            fill_bar = bars[offset]
            if fill_bar.time >= expiry:
                break
            if fill_bar.high < trigger:
                continue
            at_open = fill_bar.open >= trigger
            reference = max(fill_bar.open, trigger)
            entry_price = _decimal(reference) + _slip(reference, account)
            if not _affordable(shares, entry_price, budget):
                events.append(OrderEvent(fill_bar.time, "rejected", "Opening gap exceeds buying power",
                                         trigger, stop, shares))
                return result(reason="unaffordable_gap")
            detail = "Filled at opening reference" if at_open else "Intrabar buy-stop touch; time is bar start"
            events.append(OrderEvent(fill_bar.time, "filled", detail, trigger, stop, shares))
            position = OpenPosition(signal, bar.time, fill_bar.time, trigger, stop, shares,
                                    entry_price, reference, stop, entry_price, entry_price)
            position.observe(reference)
            trade = _advance(position, session, bars, atrs, offset, account, exit_mode,
                             next_open, next_bar_open, None, 0, events)
            return result(trade, open_position=position if trade is None else None)
        events.append(OrderEvent(expiry, "expired", "Buy stop expired without a fill", trigger, stop, shares))
        return result(reason="expired")
    return result(reason="no_bear" if signal is not None else "no_signal")


def _advance(position, session, bars, atrs, offset, account, exit_mode, next_open,
             next_bar_open, level, prior_session_count, events) -> Trade | None:
    for index in range(offset, len(bars)):
        bar = bars[index]
        if position.pending_stop is not None:
            position.active_stop = position.pending_stop
            position.pending_stop = None
        stop = position.active_stop
        entry_bar = bar.time == position.entry_time
        intrabar_entry = entry_bar and bar.open < position.entry_trigger
        if bar.low <= stop:
            reference = stop if entry_bar else min(bar.open, stop)
            if not intrabar_entry:
                position.observe(bar.open)
            return _finish(position, bar.time, reference, account, events, "protective_stop",
                           gap_possible=not entry_bar, ambiguous=intrabar_entry and bar.close > stop)
        position.observe(bar.high, bar.close if intrabar_entry else bar.low)
        atr = atrs[index]
        if (exit_mode == "trailing_stop" and level is not None and bar.close > bar.open
                and bar.high < level and atr is not None and math.isfinite(atr) and atr >= 0):
            tick = Fraction(str(account.tick_size))
            raw_stop = Fraction(str(bar.low)) - Fraction(str(atr)) / 2
            candidate = float((raw_stop // tick) * tick)
            if candidate > stop:
                effective = bars[index + 1].time if index + 1 < len(bars) else next_bar_open
                if effective is None:
                    day = session.date
                    following = sessions(day + timedelta(days=1), day + timedelta(days=14))
                    effective = following[0].open_utc
                position.pending_stop = candidate
                adjustment = StopAdjustment(bar.time, effective, stop, candidate, prior_session_count)
                position.stop_adjustments += (adjustment,)
                events.append(OrderEvent(bar.time + STEP, "stop_adjusted",
                                         f"Protective stop increase from {stop} to {candidate}",
                                         position.entry_trigger, candidate, position.shares, adjustment))
    if exit_mode == "trailing_stop":
        return None
    if exit_mode == "same_day_close":
        return _finish(position, session.close_utc, bars[-1].close, account, events, exit_mode)
    exit_time, exit_reference = next_open
    return _finish(position, exit_time, exit_reference, account, events, exit_mode, gap_possible=True)


def _finish(position, exit_time, exit_reference, account, events, reason, *,
            gap_possible=False, ambiguous=False) -> Trade:
    stop, shares, entry_price = position.active_stop, position.shares, position.entry_price
    exit_raw = _decimal(exit_reference)
    exit_price = exit_raw - _slip(exit_reference, account)
    if exit_price <= 0:
        raise ValueError("Modeled exit price is nonpositive; check the slippage settings.")
    fees = _decimal(commission(shares, entry_price)) + _decimal(commission(shares, exit_price))
    exit_gap = max(Decimal(0), _decimal(stop) - exit_raw) if gap_possible else Decimal(0)
    entry_raw = _decimal(position.entry_reference)
    position.observe(exit_raw, exit_price)
    risk = entry_price - _decimal(position.initial_stop)
    events.append(OrderEvent(exit_time, "closed", reason, position.entry_trigger, stop, shares))
    return Trade(
        position.signal_time, position.bear_time, position.entry_time, exit_time, position.entry_trigger,
        position.initial_stop, float(entry_price), float(exit_price), shares,
        (exit_price - entry_price) * shares - fees, float(fees),
        float((entry_price - entry_raw + exit_raw - exit_price) * shares),
        float((entry_raw - _decimal(position.entry_trigger) + exit_gap) * shares), reason, ambiguous,
        float((position.high - entry_price) / risk), float((entry_price - position.low) / risk),
        position.stop_adjustments,
    )
