"""Causal replay of the opening-range setup on regular-session one-minute bars."""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from math import ceil, floor, isfinite
from typing import Literal

from .rid import RidSession, calculate_rid
from .sessions import Session
from .store import Bar

RuleVersion = Literal["legacy", "revised"]


@dataclass(frozen=True)
class EntrySettings:
    enabled: bool = True
    entry_lookback: int = 4
    stop_lookback: int = 4
    entry_atr: float = 0.25
    stop_atr: float = 0.25
    risk_pct: float = 0.25


@dataclass(frozen=True)
class Entry3Settings(EntrySettings):
    min_rid_bars: int = 3


@dataclass(frozen=True)
class Settings:
    starting_balance: float = 100_000
    leverage: float = 4
    trail_lookback: int = 4
    trail_atr: float = 0.25
    tick_size: float = 0.01
    slippage_bps: float = 1
    slippage_floor: float = 0.01
    daily_atr_period: int = 20
    entry1: EntrySettings = EntrySettings()
    entry2: EntrySettings = EntrySettings(enabled=False)
    entry3: Entry3Settings = Entry3Settings(enabled=False)


class WilderATR:
    def __init__(self, period: int) -> None:
        self.period = period
        self.previous_close = None
        self.seeds = []
        self.value = None

    def update(self, bar: Bar) -> float | None:
        true_range = bar.high - bar.low
        if self.previous_close is not None:
            true_range = max(true_range, bar.high - self.previous_close, self.previous_close - bar.low)
        self.previous_close = bar.close
        if self.value is not None:
            self.value = (self.value * (self.period - 1) + true_range) / self.period
        else:
            self.seeds.append(true_range)
            if len(self.seeds) == self.period:
                self.value = sum(self.seeds) / self.period
        return self.value


@dataclass
class Trade:
    signal_time: int
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    shares: int
    pnl: float
    commission: float
    slippage: float
    atr_close_pct: float | None
    mfe_pct: float
    exit_reason: str
    ambiguous: bool
    entry_trigger: float
    initial_stop: float
    stop_updates: list[tuple[int, float]]
    gap_slippage: float
    entry_type: str
    order_id: int


def commission(shares: int, price: float) -> float:
    """IBKR Pro Fixed stock commission, in USD, per completed order."""
    fee = min(max(Decimal(shares) * Decimal("0.005"), Decimal("1")), Decimal(str(price)) * shares / 100)
    return float(fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def session_vwap(bars: list[Bar]) -> list[float | None]:
    volume = weighted = 0.0
    values = []
    for bar in bars:
        volume += bar.volume
        weighted += (bar.high + bar.low + bar.close) / 3 * bar.volume
        values.append(weighted / volume if volume else None)
    return values


def order_price(value: float, tick: float, *, up: bool) -> float:
    steps = ceil(value / tick - 1e-9) if up else floor(value / tick + 1e-9)
    return round(steps * tick, 10)


def slip(reference: float, cfg: Settings, multiplier: float) -> float:
    return max(cfg.slippage_floor, reference * cfg.slippage_bps / 10_000) * multiplier


@dataclass(frozen=True)
class OrderEvent:
    time: int
    action: str
    entry_type: str
    order_id: int | None
    signal_time: int
    entry_trigger: float | None = None
    initial_stop: float | None = None
    shares: int | None = None
    prior_order_id: int | None = None
    reason: str | None = None


@dataclass
class SessionReplay:
    trades: list[Trade]
    events: list[OrderEvent]
    ending_balance: float


@dataclass(frozen=True)
class WorkingOrder:
    id: int
    entry_type: str
    signal_time: int
    trigger: float
    initial_stop: float
    shares: int

    def event(self, time: int, action: str, *, shares: int | None = None,
              prior_id: int | None = None, reason: str | None = None) -> OrderEvent:
        return OrderEvent(time, action, self.entry_type, self.id, self.signal_time,
                          self.trigger, self.initial_stop, self.shares if shares is None else shares,
                          prior_id, reason)


@dataclass
class Position:
    order: WorkingOrder
    entry_time: int
    entry_ref: float
    entry_price: float
    shares: int
    stop: float
    best: float
    stop_updates: list[tuple[int, float]] = field(default_factory=list)
    trail_pending: bool = False

    def close(self, time: int, reference: float, reason: str, cfg: Settings,
              daily_ratio: float | None, multiplier: float, *, ambiguous: bool = False) -> Trade:
        sell_slip = slip(reference, cfg, multiplier)
        exit_price = reference - sell_slip
        if not isfinite(exit_price) or exit_price <= 0:
            raise ValueError(f"Invalid or nonpositive modeled exit price {exit_price:g} at epoch {time}. "
                             "Check the slippage settings.")
        fees = commission(self.shares, self.entry_price) + commission(self.shares, exit_price)
        exit_gap = self.stop - reference if reason == "stop" else 0.0
        gap_cost = (self.entry_ref - self.order.trigger + exit_gap) * self.shares
        modeled = (self.entry_price - self.entry_ref + sell_slip) * self.shares + gap_cost
        best = self.best if reason == "stop" else max(self.best, reference)
        return Trade(
            signal_time=self.order.signal_time, entry_time=self.entry_time, entry_price=self.entry_price,
            exit_time=time, exit_price=exit_price, shares=self.shares,
            pnl=(exit_price - self.entry_price) * self.shares - fees, commission=fees, slippage=modeled,
            atr_close_pct=daily_ratio, mfe_pct=(best - self.entry_price) / self.entry_price * 100,
            exit_reason=reason, ambiguous=ambiguous, entry_trigger=self.order.trigger,
            initial_stop=self.order.initial_stop, stop_updates=self.stop_updates, gap_slippage=gap_cost,
            entry_type=self.order.entry_type, order_id=self.order.id,
        )


def replay_session(
    session: Session, bars: list[Bar], atrs: list[float | None], equity: float,
    cfg: Settings, daily_ratio: float | None, slippage_multiplier: float,
    *, vwaps: list[float | None] | None = None, rule_version: RuleVersion = "legacy",
    rid: RidSession | None = None,
) -> SessionReplay:
    opening_high = max(b.high for b in bars[:15])
    vwap = session_vwap(bars) if vwaps is None else vwaps
    entry_deadline = min(150, len(bars))
    exit_index = min(210, len(bars))
    working = position = None
    trades, events = [], []
    next_id = 1
    revised = rule_version == "revised"
    if cfg.entry3.enabled and rid is None:
        rid = calculate_rid(bars)
    entry3_armed = False

    for i, bar in enumerate(bars[:exit_index + 1]):
        if i == entry_deadline and working is not None:
            events.append(working.event(bar.time, "canceled", reason="noon"))
            working = None
        if i == exit_index:
            if position is not None:
                trade = position.close(bar.time, bar.open, "time", cfg, daily_ratio, slippage_multiplier)
                trades.append(trade)
                equity += trade.pnl
                position = None
            break
        if working is not None and bar.high >= working.trigger:
            order, working = working, None
            reference = max(bar.open, order.trigger)
            fill = reference + slip(reference, cfg, slippage_multiplier)
            if not isfinite(fill) or fill <= 0:
                raise ValueError(f"Invalid modeled entry price {fill:g} at epoch {bar.time}. "
                                 "Check the slippage settings.")
            buying_power = Fraction(str(equity)) * Fraction(str(cfg.leverage))
            shares = min(order.shares, buying_power // Fraction(str(fill)))
            if shares > 0:
                position = Position(order, bar.time, reference, fill, shares, order.initial_stop, fill)
                events.append(order.event(bar.time, "filled", shares=shares))
            else:
                events.append(order.event(bar.time, "skipped", shares=0, reason="fill_buying_power"))

        if position is not None and bar.low <= position.stop:
            same_minute = position.entry_time == bar.time
            ambiguous = same_minute and bar.open < position.order.trigger and bar.close > position.stop
            reference = position.stop if same_minute else min(bar.open, position.stop)
            if not same_minute:
                position.best = max(position.best, bar.open)
            trade = position.close(bar.time, reference, "stop", cfg, daily_ratio, slippage_multiplier,
                                   ambiguous=ambiguous)
            trades.append(trade)
            equity += trade.pnl
            position = None

        atr = atrs[i]
        entry3_signal = False
        if cfg.entry3.enabled and rid is not None and 4 <= i < entry_deadline:
            if entry3_armed and bar.close < bar.open:
                entry3_signal = True
                entry3_armed = False
            elif (not entry3_armed and rid.prior_length[i] >= cfg.entry3.min_rid_bars
                  and rid.prior_upper[i] is not None and vwap[i] is not None
                  and bar.close > bar.open and bar.close > rid.prior_upper[i] and bar.high < vwap[i]):
                entry3_armed = True
        if position is not None:
            position.best = max(position.best, bar.high)
            bear_below_vwap = vwap[i] is not None and bar.close < bar.open and bar.high < vwap[i]
            update_stop = bear_below_vwap
            if revised:
                confirms_trail = position.trail_pending and bar.close > bar.open
                update_stop = confirms_trail and cfg.trail_lookback > 0
                if confirms_trail:
                    position.trail_pending = False
                elif bear_below_vwap:
                    position.trail_pending = True
            if update_stop and i >= cfg.trail_lookback and i + 1 < exit_index and atr is not None:
                lookback_end = i if revised else i + 1
                candidate = order_price(min(b.low for b in bars[i - cfg.trail_lookback:lookback_end])
                                        - cfg.trail_atr * atr, cfg.tick_size, up=False)
                if candidate > position.stop:
                    position.stop = candidate
                    position.stop_updates.append((bar.time + 60, candidate))
            continue
        if i >= entry_deadline:
            continue
        if cfg.entry1.enabled and i >= 15 and bar.close > bar.open and bar.low > opening_high:
            entry_type, settings = "entry1", cfg.entry1
        elif (cfg.entry2.enabled and i >= 4 and (bar.close <= bar.open if revised else bar.close < bar.open)
              and all(b.close > b.open for b in bars[i - 4:i])
              and (not revised or any(vwap[j] is not None and bars[j].high > vwap[j] for j in (i - 1, i)))):
            entry_type, settings = "entry2", cfg.entry2
        elif entry3_signal:
            entry_type, settings = "entry3", cfg.entry3
        else:
            continue

        entry_lookback = 4 if entry_type == "entry3" else settings.entry_lookback
        effective = bar.time + 60
        if i + 1 == entry_deadline:
            events.append(OrderEvent(effective, "skipped", entry_type, None, bar.time, reason="no_active_window"))
            continue
        if atr is None or i < max(entry_lookback, settings.stop_lookback):
            events.append(OrderEvent(effective, "skipped", entry_type, None, bar.time, reason="warmup"))
            continue
        trigger = order_price(max(b.high for b in bars[i - entry_lookback:i + 1])
                              + settings.entry_atr * atr, cfg.tick_size, up=True)
        stop = order_price(min(b.low for b in bars[i - settings.stop_lookback:i + 1])
                           - settings.stop_atr * atr, cfg.tick_size, up=False)
        if not 0 < stop < trigger:
            events.append(OrderEvent(effective, "skipped", entry_type, None, bar.time, trigger, stop,
                                     reason="invalid_stop"))
            continue
        # The order levels are tick-rounded. Preserve their decimal distance through flooring.
        balance = Fraction(str(equity))
        trigger_price = Fraction(str(trigger))
        distance = trigger_price - Fraction(str(stop))
        risk_budget = balance * Fraction(str(settings.risk_pct)) / 100
        buying_power = balance * Fraction(str(cfg.leverage))
        shares = min(risk_budget // distance, buying_power // trigger_price)
        if shares <= 0 or (working is not None and trigger >= working.trigger):
            reason = "zero_shares" if shares <= 0 else "not_lower"
            events.append(OrderEvent(effective, "skipped", entry_type, None, bar.time, trigger, stop,
                                     max(0, shares), reason=reason))
            continue
        order = WorkingOrder(next_id, entry_type, bar.time, trigger, stop, shares)
        next_id += 1
        if working is None:
            events.append(order.event(effective, "placed"))
        else:
            events.append(order.event(effective, "replaced", prior_id=working.id))
        working = order

    if working is not None:
        events.append(working.event(session.close_utc, "canceled", reason="session_close"))
    if position is not None:
        trade = position.close(session.close_utc, bars[-1].close, "early_close", cfg, daily_ratio,
                               slippage_multiplier)
        trades.append(trade)
        equity += trade.pnl
    return SessionReplay(trades, events, equity)
