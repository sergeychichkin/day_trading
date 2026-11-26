"""Month payloads joining TWS SPY bars with exact Yahoo daily levels."""

import json
import math
import re
from datetime import date, timedelta
from pathlib import Path

from .spy_5m_store import START, STEP, FiveMinuteStore

MONTH = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])", flags=re.ASCII)


def month_index(store: FiveMinuteStore) -> dict:
    bounds = store.bounds()
    latest = bounds["latest"]
    through = bounds["published_through"] or bounds["target"]
    months = []
    current = date.fromisoformat(bounds["published_start"]).replace(day=1) if bounds["published_start"] else START
    last = date.fromisoformat(through).replace(day=1) if through else None
    while last and current <= last:
        months.append(current.strftime("%Y-%m"))
        if current == last:
            break
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return {"months": months, "default_month": latest[:7] if latest else None}


def _daily_levels(path):
    try:
        rows = json.loads(path.read_bytes())["bars"]
        if not isinstance(rows, list) or not all(isinstance(row, dict) and isinstance(row.get("time"), str)
                                                 for row in rows):
            raise ValueError("Invalid SPY daily snapshot.")
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


def _session_levels(session, daily, source_status):
    if source_status != "available":
        return None, None, f"snapshot_{source_status}"
    row = daily.get(session["date"])
    if row is None:
        return None, None, "no_daily_row"
    bars = session["bars"]
    pair = daily_price_pair(row)
    if (pair is None or not bars or bars[0]["time"] != session["open_utc"]
            or bars[-1]["time"] + STEP != session["close_utc"]):
        return None, None, "price_unverified"
    opening, close = bars[0]["open"], bars[-1]["close"]
    if price_basis_mismatch(opening, close, row):
        return None, None, "price_mismatch"
    if max(abs(opening / pair[0] - 1), abs(close / pair[1] - 1)) > 0.05:
        return None, None, "price_unverified"

    levels = []
    invalid = False
    for field, low, high in (("upper", 1, 2), ("lower", 0.5, 1)):
        raw = row.get(field)
        price = _positive_price(raw)
        if raw is not None and (price is None or not low <= price / pair[0] <= high):
            invalid, price = True, None
        levels.append(price)
    status = "invalid_levels" if invalid else "missing_levels" if None in levels else "available"
    return *levels, status


def _add_vwap(session):
    expected = session["open_utc"]
    weighted = volume = 0.0
    intact = True
    for bar in session["bars"]:
        intact = intact and bar["time"] == expected
        if bar["volume"] > 0:
            if bar["wap"] is None:
                intact = False
            elif intact:
                volume += bar["volume"]
                weighted += bar["wap"] * bar["volume"]
        bar["vwap"] = weighted / volume if intact and volume else None
        expected = bar["time"] + STEP


def month_payload(store: FiveMinuteStore, daily_cache: Path, month: str) -> dict:
    if not MONTH.fullmatch(month) or not "2000-01" <= month < "9999-12":
        raise ValueError("Enter a SPY month from January 2000 onward.")
    data = store.read_month(month)
    rows = data["sessions"]
    daily, source = _daily_levels(daily_cache)
    status = source
    for session in rows:
        session["upper"], session["lower"], session["daily_level_status"] = _session_levels(session, daily, source)
        if session["daily_level_status"] == "price_mismatch":
            status = "price_mismatch"
        _add_vwap(session)
    expected = sum((row["close_utc"] - row["open_utc"]) // STEP for row in rows)
    received = sum(len(row["bars"]) for row in rows)
    return {
        "month": month, "sessions": rows, "daily_status": status, "through": data["target"],
        "published_start": data["published_start"], "published_through": data["published_through"],
        "requested_through": data["requested_through"],
        "coverage": {
            "expected": expected, "received": received, "missing": expected - received,
            "unverified_sessions": sum(not row["verified"] for row in rows),
            "conflicting": sum(len(row["conflict_times"]) for row in rows),
        },
    }
