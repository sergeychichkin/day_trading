"""Calculation helpers shared by the five-minute replay."""

from decimal import Decimal, ROUND_HALF_UP
from math import ceil, floor
from typing import Protocol


class OHLC(Protocol):
    @property
    def high(self) -> float: ...
    @property
    def low(self) -> float: ...
    @property
    def close(self) -> float: ...


class WilderATR:
    def __init__(self, period: int) -> None:
        self.period = period
        self.previous_close = None
        self.seeds = []
        self.value = None

    def update(self, bar: OHLC) -> float | None:
        return self.update_prices(bar.high, bar.low, bar.close)

    def update_prices(self, high: float, low: float, close: float) -> float | None:
        true_range = high - low
        if self.previous_close is not None:
            true_range = max(true_range, high - self.previous_close, self.previous_close - low)
        self.previous_close = close
        if self.value is not None:
            self.value = (self.value * (self.period - 1) + true_range) / self.period
        else:
            self.seeds.append(true_range)
            if len(self.seeds) == self.period:
                self.value = sum(self.seeds) / self.period
        return self.value


def commission(shares: int, price: float) -> float:
    """IBKR Pro Fixed stock commission, in USD, per completed order."""
    fee = min(max(Decimal(shares) * Decimal("0.005"), Decimal("1")), Decimal(str(price)) * shares / 100)
    return float(fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def order_price(value: float, tick: float, *, up: bool) -> float:
    steps = ceil(value / tick - 1e-9) if up else floor(value / tick + 1e-9)
    return round(steps * tick, 10)
