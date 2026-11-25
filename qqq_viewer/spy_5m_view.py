"""Month payloads joining TWS SPY bars with exact Yahoo daily levels."""

import json
import math
import re
from datetime import date, timedelta
from pathlib import Path

from .spy_5m_store import START, STEP, FiveMinuteStore

MONTH = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])", flags=re.ASCII)


def month_index(store: FiveMinuteStore) -> dict:
    latest = store.latest_date()
    through = store.target_end_date()
    months = []
    current = START
    while through and current <= through:
        months.append(current.strftime("%Y-%m"))
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return {"months": months, "default_month": latest.strftime("%Y-%m") if latest else None}


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


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def price_basis_mismatch(opening: float, close: float, daily: dict) -> tuple[float, float] | None:
    daily_open, daily_close = _number(daily.get("open")), _number(daily.get("close"))
    if not daily_open or not daily_close or daily_open < 0 or daily_close < 0:
        return None
    a, b = opening / daily_open, close / daily_close
    # Ignore normal venue/auction differences; require a common factor
    # more than 5% from parity with open and close agreeing within 1%.
    return (a, b) if min(abs(a - 1), abs(b - 1)) > 0.05 and abs(a / b - 1) <= 0.01 else None


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
    daily, status = _daily_levels(daily_cache)
    for session in rows:
        row = daily.get(session["date"], {})
        bars = session["bars"]
        mismatch = (bars and bars[0]["time"] == session["open_utc"]
                    and bars[-1]["time"] + STEP == session["close_utc"]
                    and price_basis_mismatch(bars[0]["open"], bars[-1]["close"], row))
        if mismatch:
            session["upper"] = session["lower"] = None
            status = "price_mismatch"
        else:
            session["upper"], session["lower"] = _number(row.get("upper")), _number(row.get("lower"))
        _add_vwap(session)
    expected = sum((row["close_utc"] - row["open_utc"]) // STEP for row in rows)
    received = sum(len(row["bars"]) for row in rows)
    return {
        "month": month, "sessions": rows, "daily_status": status, "through": data["target"],
        "coverage": {
            "expected": expected, "received": received, "missing": expected - received,
            "unverified_sessions": sum(not row["verified"] for row in rows),
            "conflicting": sum(len(row["conflict_times"]) for row in rows),
        },
    }
