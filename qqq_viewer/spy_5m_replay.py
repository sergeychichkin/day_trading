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
StopSource = Literal[3, 4, 5]


@dataclass(frozen=True)
class EntrySettings:
    entry1_start_time: time = time(9, 30)
    entry1_end_time: time = time(9, 40)
    entry_atr_buffer: float = 0.25


@dataclass(frozen=True)
class AccountSettings:
    starting_balance: float | Decimal = 100_000
    risk_pct: float | Decimal = 1
    cash_cap: float | Decimal = 1
    tick_size: float = 0.01
    slippage_bps: float | Decimal = 1
    slippage_floor: float | Decimal = 0.01


@dataclass(frozen=True)
class StopCandidate:
    source: StopSource
    trigger_time: int
    price: float


@dataclass(frozen=True)
class StopAdjustment:
    candle_time: int
    effective_time: int
    old_stop: float
    new_stop: float
    prior_session_count: int
    source: StopSource


@dataclass(frozen=True)
class OrderEvent:
    time: int
    action: str
    detail: str
    trigger: float | None = None
    stop: float | None = None
    shares: int | None = None
    adjustment: StopAdjustment | None = None
    order_id: int | None = None


@dataclass(frozen=True)
class PendingEntry:
    signal_time: int
    bear_time: int
    trigger: float
    stop: float
    shares: int
    budget: Decimal
    activation_time: int
    expiry_time: int

    def event(self, when, action, detail) -> OrderEvent:
        return OrderEvent(when, action, detail, self.trigger, self.stop, self.shares,
                          order_id=self.bear_time + STEP)


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
    pending_stop: StopCandidate | None = None
    stop_adjustments: tuple[StopAdjustment, ...] = ()
    active_stop_source: StopSource | None = None

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
    stop_source: StopSource | None = None


@dataclass(frozen=True)
class DayResult:
    trade: Trade | None
    events: list[OrderEvent]
    skip_reason: str | None = None
    position: OpenPosition | None = None
    later_trade: Trade | None = None
    pending_entry: PendingEntry | None = None

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


def session_vwap_totals(bars: list[FiveMinuteBar]):
    weighted = volume = Decimal(0)
    for bar in bars:
        if bar.volume:
            count = _decimal(bar.volume)
            weighted += _decimal(bar.wap) * count
            volume += count
        yield weighted, volume


def _fill_entry(order, bars, offset, account, events):
    for index in range(offset, len(bars)):
        bar = bars[index]
        if bar.time >= order.expiry_time:
            break
        if bar.time < order.activation_time or bar.high < order.trigger:
            continue
        reference = max(bar.open, order.trigger)
        entry_price = _decimal(reference) + _slip(reference, account)
        if not _affordable(order.shares, entry_price, order.budget):
            events.append(order.event(bar.time, "rejected", "Opening gap exceeds buying power"))
            return None, index
        detail = "Filled at opening reference" if bar.open >= order.trigger else "Intrabar buy-stop touch; time is bar start"
        events.append(order.event(bar.time, "filled", detail))
        position = OpenPosition(order.signal_time, order.bear_time, bar.time, order.trigger, order.stop,
                                order.shares, entry_price, reference, order.stop, entry_price, entry_price)
        position.observe(reference)
        return position, index
    return None, None


def replay_day(session: Session, bars: list[FiveMinuteBar], atrs: list[float | None],
               upper: float | None, equity: float | Decimal, entry: EntrySettings, account: AccountSettings,
               exit_mode: ExitMode, *, session_lower: float | None, next_open: tuple[int, float] | None = None,
               position: OpenPosition | None = None,
               prior_lowers: tuple[float | None, float | None] = (None, None),
               next_bar_open: int | None = None, pending_entry: PendingEntry | None = None,
               holding_day: int | None = None, exit3_enabled: bool = True, exit3_max_holding_days: int = 9,
               opening_stop_candidates: tuple[StopCandidate, ...] = ()) -> DayResult:
    events: list[OrderEvent] = []
    if exit_mode not in {"same_day_close", "next_session_open", "trailing_stop"}:
        raise ValueError(f"Unknown SPY exit mode: {exit_mode}")
    if len(atrs) != len(bars):
        raise ValueError("ATR and SPY bars must be aligned.")
    if exit_mode == "next_session_open" and next_open is None and pending_entry is None:
        return DayResult(None, events, "next_open_unavailable")
    if position is not None and exit_mode != "trailing_stop":
        raise ValueError("Only the managed-stop scenario carries an open position.")

    carried_trade = None
    first_signal_time = session.open_utc
    if position is not None:
        position = replace(position)
        if holding_day is None:
            entry_date = datetime.fromtimestamp(position.entry_time, NEW_YORK).date()
            holding_day = len(sessions(entry_date, session.date))
        level = None
        if exit3_enabled and 3 <= holding_day <= exit3_max_holding_days:
            level = max((value for value in prior_lowers if value is not None), default=None)
        carried_trade = _advance(position, session, bars, atrs, 0, account, exit_mode, next_open, events,
                                 level=level, last_exit3_day=holding_day == exit3_max_holding_days,
                                 opening_stop_candidates=opening_stop_candidates)
        if carried_trade is None:
            return DayResult(None, events, position=position)
        first_signal_time = carried_trade.exit_time
        equity = _decimal(equity) + carried_trade.pnl

    if pending_entry is not None:
        if pending_entry.activation_time == session.open_utc:
            events.append(pending_entry.event(session.open_utc, "activated", "Eligible from this bar's open"))
        overnight_position, fill_index = _fill_entry(pending_entry, bars, 0, account, events)
        if fill_index is None:
            events.append(pending_entry.event(pending_entry.expiry_time, "expired", "Buy stop expired without a fill"))
        elif overnight_position is not None:
            carried_trade = _advance(overnight_position, session, bars, atrs, fill_index, account, exit_mode,
                                     next_open, events)
            if carried_trade is None:
                return DayResult(None, events, position=overnight_position)
            first_signal_time = carried_trade.exit_time
            equity = _decimal(equity) + carried_trade.pnl
            if first_signal_time >= session.close_utc:
                return DayResult(carried_trade, events)

    def result(trade=None, reason=None, open_position=None, pending=None):
        return DayResult(carried_trade or trade, events, reason, open_position,
                         trade if carried_trade is not None else None, pending)

    if exit_mode == "next_session_open" and next_open is None:
        return result(reason="next_open_unavailable")
    if upper is None:
        return result(reason="daily_upper_unavailable")
    if equity <= 0:
        return result(reason="cash_unavailable")

    signal = None
    confirmed = False
    session_high = 0.0
    daily_upper = _decimal(upper)
    for index, (bar, atr, (weighted, volume)) in enumerate(zip(bars, atrs, session_vwap_totals(bars), strict=True)):
        session_high = max(session_high, bar.high)
        if bar.time < first_signal_time:
            continue
        if signal is None:
            local = datetime.fromtimestamp(bar.time, NEW_YORK).time()
            if entry.entry1_start_time <= local <= entry.entry1_end_time and bar.close > max(bar.open, upper):
                signal = bar.time
                events.append(OrderEvent(bar.time + STEP, "signal", "Bull closed above the daily upper level"))
            else:
                continue
        above_upper = volume > 0 and weighted > daily_upper * volume
        confirmed = confirmed or above_upper
        if not confirmed or bar.close >= bar.open:
            continue
        if not above_upper:
            return result(reason="vwap_below_upper_at_bear")

        submitted = bar.time + STEP
        if atr is None or not math.isfinite(atr) or atr < 0:
            events.append(OrderEvent(submitted, "rejected", "ATR(20) not available at the bear close"))
            return result(reason="atr_unavailable")
        trigger = order_price(session_high + entry.entry_atr_buffer * atr, account.tick_size, up=True)
        if session_lower is None:
            events.append(OrderEvent(submitted, "rejected", "Signal session daily Lower is unavailable", trigger))
            return result(reason="daily_lower_unavailable")
        stop = None
        if math.isfinite(session_lower):
            tick = Fraction(str(account.tick_size))
            stop = float((Fraction(str(session_lower)) // tick) * tick)
        if stop is None or not 0 < stop < trigger:
            events.append(OrderEvent(submitted, "rejected", "Daily Lower must round to a positive stop below the trigger",
                                     trigger, stop))
            return result(reason="invalid_stop")
        shares, budget = _size(trigger, stop, equity, account)
        if not shares:
            events.append(OrderEvent(submitted, "rejected", "Position rounds to zero shares", trigger, stop, shares))
            return result(reason="cash_unavailable")
        upcoming = next_bar_open
        if upcoming is None:
            day = session.date
            upcoming = sessions(day + timedelta(days=1), day + timedelta(days=14))[0].open_utc
        activate = submitted if submitted < session.close_utc else upcoming
        order = PendingEntry(signal, bar.time, trigger, stop, shares, budget, activate, upcoming + STEP)
        events.append(order.event(submitted, "submitted", "Buy stop and fixed protective stop"))
        if activate == upcoming:
            return result(pending=order)
        events.append(order.event(activate, "activated", "Eligible from this bar's open"))
        position, fill_index = _fill_entry(order, bars, index + 1, account, events)
        if fill_index is None:
            return result(pending=order)
        if position is None:
            return result(reason="unaffordable_gap")
        trade = _advance(position, session, bars, atrs, fill_index, account, exit_mode, next_open, events)
        return result(trade, open_position=position if trade is None else None)
    return result(reason="no_signal" if signal is None else "no_bear" if confirmed else "no_vwap_confirmation")


def _apply_stop(position, time, candidates, events):
    winner = max(candidates, key=lambda item: (item.price, -item.source), default=None)
    if winner is None or winner.price <= position.active_stop:
        return None
    old_stop = position.active_stop
    position.active_stop = winner.price
    position.active_stop_source = winner.source
    adjustment = StopAdjustment(winner.trigger_time, time, old_stop, winner.price,
                                2 if winner.source == 3 else 0, winner.source)
    position.stop_adjustments += (adjustment,)
    events.append(OrderEvent(time, "stop_adjusted", f"Exit {winner.source} raised stop from {old_stop} to {winner.price}",
                             position.entry_trigger, winner.price, position.shares, adjustment))
    return winner.source


def _advance(position, session, bars, atrs, offset, account, exit_mode, next_open, events, *,
             level=None, last_exit3_day=False, opening_stop_candidates=()) -> Trade | None:
    first_eligible = datetime.combine(session.date, time(9, 45), NEW_YORK).timestamp() if level is not None else None
    queued = opening_stop_candidates
    for index in range(offset, len(bars)):
        bar = bars[index]
        if position.pending_stop is not None:
            queued += (position.pending_stop,)
            position.pending_stop = None
        if queued:
            if _apply_stop(position, bar.time, queued, events) == 3:
                first_eligible = None
            queued = ()
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
        if (first_eligible is not None and bar.time >= first_eligible and bar.close > bar.open
                and bar.high < level and atr is not None and math.isfinite(atr) and atr >= 0):
            if last_exit3_day and bar.time + STEP >= session.close_utc:
                continue
            tick = Fraction(str(account.tick_size))
            raw_stop = Fraction(str(bar.low)) - Fraction(str(atr)) / 2
            candidate = float((raw_stop // tick) * tick)
            if candidate > stop:
                position.pending_stop = StopCandidate(3, bar.time, candidate)
                first_eligible = None
    if exit_mode == "trailing_stop" or (exit_mode == "next_session_open" and next_open is None):
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
    source = position.active_stop_source if reason == "protective_stop" else None
    detail = (f"Exit {source}" if source is not None else "Initial SL") if reason == "protective_stop" else reason
    events.append(OrderEvent(exit_time, "closed", detail, position.entry_trigger, stop, shares))
    return Trade(
        position.signal_time, position.bear_time, position.entry_time, exit_time, position.entry_trigger,
        position.initial_stop, float(entry_price), float(exit_price), shares,
        (exit_price - entry_price) * shares - fees, float(fees),
        float((entry_price - entry_raw + exit_raw - exit_price) * shares),
        float((entry_raw - _decimal(position.entry_trigger) + exit_gap) * shares), reason, ambiguous,
        float((position.high - entry_price) / risk), float((entry_price - position.low) / risk),
        position.stop_adjustments, source,
    )
