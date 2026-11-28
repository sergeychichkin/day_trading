"""Read daily reference prices and check the basis of acquired intraday bars."""

import json
import math


def load_daily_rows(path):
    try:
        rows = json.loads(path.read_bytes())["bars"]
        if not isinstance(rows, list) or not all(isinstance(row, dict) and isinstance(row.get("time"), str)
                                                 for row in rows):
            raise ValueError("Invalid daily snapshot.")
    except FileNotFoundError:
        return {}, "missing"
    except (OSError, ValueError, KeyError, TypeError):
        return {}, "unreadable"

    return {row["time"]: row for row in rows}, "available"


def _positive_price(value):
    try:
        return value if type(value) in (int, float) and math.isfinite(value) and value > 0 else None
    except OverflowError:
        return None


def daily_price_pair(daily: dict) -> tuple[float, float] | None:
    opening, close = _positive_price(daily.get("open")), _positive_price(daily.get("close"))
    return (opening, close) if opening is not None and close is not None else None


def price_basis_mismatch(opening: float, close: float, daily: dict) -> tuple[float, float] | None:
    pair = daily_price_pair(daily)
    if pair is None:
        return None
    a, b = opening / pair[0], close / pair[1]
    # Ignore normal venue/auction differences; require a common factor
    # more than 5% from parity with open and close agreeing within 1%.
    if math.isfinite(a) and math.isfinite(b) and min(abs(a - 1), abs(b - 1)) > 0.05 and abs(a - b) <= 0.01 * b:
        return a, b
    return None
