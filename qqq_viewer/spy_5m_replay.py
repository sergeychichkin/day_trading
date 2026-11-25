"""One-way Entry 1 replay using only data observable at each decision."""

import math
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from typing import Literal

from .opening_range import commission, order_price
from .sessions import NEW_YORK, Session
from .spy_5m_store import STEP, FiveMinuteBar

ExitMode = Literal["same_day_close", "next_session_open"]


@dataclass(frozen=True)
class EntrySettings:
    entry1_start_time: time = time(9, 30)
    entry1_end_time: time = time(9, 40)
    entry_atr_buffer: float = 0.25
    stop_atr_buffer: float = 0.25
    entry_order_valid_minutes: int = 120


@dataclass(frozen=True)
class AccountSettings:
    starting_balance: float = 100_000
    risk_pct: float = 1
    cash_cap: float = 1
    tick_size: float = 0.01
    slippage_bps: float = 1
    slippage_floor: float = 0.01


@dataclass(frozen=True)
class OrderEvent:
    time: int
    action: str
    detail: str
    trigger: float | None = None
    stop: float | None = None
    shares: int | None = None


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
    pnl: float
    commission: float
    slippage: float
    gap_slippage: float
    exit_reason: str
    ambiguous: bool


@dataclass(frozen=True)
class DayResult:
    trade: Trade | None
    events: list[OrderEvent]
    skip_reason: str | None = None


def _decimal(value) -> Decimal:
    return Decimal(str(value))


def _slip(reference: float, account: AccountSettings) -> Decimal:
    return max(_decimal(account.slippage_floor), _decimal(reference) * _decimal(account.slippage_bps) / 10_000)


def _affordable(shares: int, price: Decimal, budget: Decimal) -> bool:
    return price * shares + _decimal(commission(shares, price)) <= budget


def _size(trigger: float, stop: float, equity: float, account: AccountSettings) -> tuple[int, Decimal]:
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
               upper: float | None, equity: float, entry: EntrySettings, account: AccountSettings,
               exit_mode: ExitMode, *, next_open: tuple[int, float] | None = None) -> DayResult:
    events: list[OrderEvent] = []
    if exit_mode not in {"same_day_close", "next_session_open"}:
        raise ValueError(f"Unknown SPY exit mode: {exit_mode}")
    if len(atrs) != len(bars):
        raise ValueError("ATR and SPY bars must be aligned.")
    if exit_mode == "next_session_open" and next_open is None:
        return DayResult(None, events, "next_open_unavailable")
    if upper is None:
        return DayResult(None, events, "daily_upper_unavailable")
    if equity <= 0:
        return DayResult(None, events, "cash_unavailable")

    signal = None
    day_low = math.inf
    for index, (bar, atr) in enumerate(zip(bars, atrs, strict=True)):
        day_low = min(day_low, bar.low)
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
            return DayResult(None, events, "no_execution_window")
        if atr is None or not math.isfinite(atr) or atr < 0:
            events.append(OrderEvent(activate, "rejected", "ATR(20) not available at the bear close"))
            return DayResult(None, events, "atr_unavailable")
        trigger = order_price(bar.high + entry.entry_atr_buffer * atr, account.tick_size, up=True)
        stop = order_price(day_low - entry.stop_atr_buffer * atr, account.tick_size, up=False)
        if not 0 < stop < trigger:
            events.append(OrderEvent(activate, "rejected", "Nonpositive stop or stop distance", trigger, stop))
            return DayResult(None, events, "invalid_stop")
        shares, budget = _size(trigger, stop, equity, account)
        if not shares:
            events.append(OrderEvent(activate, "rejected", "Position rounds to zero shares", trigger, stop, shares))
            return DayResult(None, events, "cash_unavailable")
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
                return DayResult(None, events, "unaffordable_gap")
            detail = "Filled at opening reference" if at_open else "Intrabar buy-stop touch; time is bar start"
            events.append(OrderEvent(fill_bar.time, "filled", detail, trigger, stop, shares))
            trade = _close(session, bars, offset, signal, bar.time, trigger, stop, shares, entry_price,
                           reference, account, exit_mode, next_open)
            events.append(OrderEvent(trade.exit_time, "closed", trade.exit_reason, trigger, stop, shares))
            return DayResult(trade, events)
        events.append(OrderEvent(expiry, "expired", "Buy stop expired without a fill", trigger, stop, shares))
        return DayResult(None, events, "expired")
    return DayResult(None, events, "no_bear" if signal is not None else "no_signal")


def _close(session, bars, offset, signal, bear, trigger, stop, shares, entry_price,
           entry_reference, account, exit_mode, next_open) -> Trade:
    entry_bar = bars[offset]
    ambiguous = False
    stop_gap_possible = False
    reason = "protective_stop"
    for index in range(offset, len(bars)):
        bar = bars[index]
        if bar.low <= stop:
            exit_time = bar.time
            if index == offset:
                # If entry was intrabar, the opening print predates it.
                exit_reference = stop
                ambiguous = entry_bar.open < trigger
            else:
                exit_reference = min(bar.open, stop)
                stop_gap_possible = True
            break
    else:
        if exit_mode == "same_day_close":
            exit_time, exit_reference, reason = session.close_utc, bars[-1].close, "same_day_close"
        else:
            exit_time, exit_reference = next_open
            stop_gap_possible = True
            reason = "next_session_open"
    exit_raw = _decimal(exit_reference)
    exit_price = exit_raw - _slip(exit_reference, account)
    if exit_price <= 0:
        raise ValueError("Modeled exit price is nonpositive; check the slippage settings.")
    fees = _decimal(commission(shares, entry_price)) + _decimal(commission(shares, exit_price))
    exit_gap = max(Decimal(0), _decimal(stop) - exit_raw) if stop_gap_possible else Decimal(0)
    entry_raw = _decimal(entry_reference)
    return Trade(signal, bear, entry_bar.time, exit_time, trigger, stop, float(entry_price), float(exit_price), shares,
                 float((exit_price - entry_price) * shares - fees), float(fees),
                 float((entry_price - entry_raw + exit_raw - exit_price) * shares),
                 float((entry_raw - _decimal(trigger) + exit_gap) * shares), reason, ambiguous)
