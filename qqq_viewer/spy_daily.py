"""Download finished SPY daily bars and build the local chart snapshot."""

import argparse
import json
import math
import sys
from collections import deque
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep

import pandas as pd
import pandas_market_calendars as markets
from filelock import FileLock

from .sessions import NEW_YORK

START = date(2000, 1, 1)
DEFAULT_CACHE = Path("data/spy_daily.json")
OHLCV = ["Open", "High", "Low", "Close", "Volume"]
DAILY_IDENTITY = {"schema": "yahoo-daily-v1", "ticker": "SPY", "source": "Yahoo Finance"}


def _completed_dates(now: datetime) -> list[date]:
    if now.utcoffset() is None:
        raise ValueError("A timezone-aware cutoff is required.")
    schedule = markets.get_calendar("NYSE").schedule(START, now.astimezone(NEW_YORK).date())
    return [day.date() for day in schedule.index[schedule["market_close"] <= now]]


def _normalize(frame: pd.DataFrame, expected: set[date]) -> dict[date, tuple]:
    if frame.empty:
        raise ValueError("Yahoo Finance returned no SPY daily bars.")
    if isinstance(frame.columns, pd.MultiIndex):
        for level in range(frame.columns.nlevels):
            if "SPY" in frame.columns.get_level_values(level):
                frame = frame.xs("SPY", level=level, axis=1)
                break
    if not set(OHLCV).issubset(frame.columns):
        raise ValueError("Yahoo Finance did not return all SPY OHLCV columns.")

    bars = {}
    for timestamp, *raw in frame[OHLCV].itertuples():
        day = timestamp.date()
        if day not in expected:
            continue
        try:
            open_, high, low, close, volume = (float(value) for value in raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid Yahoo Finance SPY bar for {day}.") from exc
        if (day in bars or not all(math.isfinite(value) for value in (open_, high, low, close, volume))
                or not 0 < low <= min(open_, close) <= max(open_, close) <= high or volume < 0):
            raise ValueError(f"Invalid or duplicate Yahoo Finance SPY bar for {day}.")
        bars[day] = (open_, high, low, close, volume)
    if not bars:
        raise ValueError("Yahoo Finance returned no completed SPY daily bars.")
    return bars


def _snapshot(dates: list[date], source: dict[date, tuple], ticker: str = "SPY") -> dict:
    bars, missing = [], []
    highs, lows = deque(maxlen=10), deque(maxlen=10)
    volumes, seed = deque(maxlen=20), []
    ema, direction = None, None
    for day in dates:
        stamp = day.isoformat()
        if day not in source:
            missing.append(stamp)
            highs.clear()
            lows.clear()
            volumes.clear()
            seed.clear()
            ema, direction = None, None
            continue

        open_, high, low, close, volume = source[day]
        upper = open_ * (1 + sum(highs) / 10) if len(highs) == 10 else None
        lower = open_ * (1 - sum(lows) / 10) if len(lows) == 10 else None
        highs.append((high - open_) / open_)
        lows.append((open_ - low) / open_)
        volumes.append(volume)
        if ema is None:
            seed.append(close)
            if len(seed) == 20:
                ema, direction = sum(seed) / 20, "neutral"
        else:
            updated = ema + (close - ema) * (2 / 21)
            if updated > ema:
                direction = "up"
            elif updated < ema:
                direction = "down"
            ema = updated

        bars.append({
            "time": stamp, "open": open_, "high": high, "low": low, "close": close, "volume": volume,
            "volume_sma_20": sum(volumes) / 20 if len(volumes) == 20 else None,
            "ema_20": ema, "ema_direction": direction, "upper": upper, "lower": lower,
        })
    return {**DAILY_IDENTITY, "ticker": ticker, "bars": bars, "missing_sessions": missing, "through": dates[-1].isoformat()}


def _write_snapshot(path: Path, snapshot: dict) -> None:
    with NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(snapshot, file, separators=(",", ":"), allow_nan=False)
            file.close()
            for delay in (0.025, 0.05, 0.1, 0.2, None):
                try:
                    temporary.replace(path)
                    break
                except PermissionError as exc:
                    if delay is None or getattr(exc, "winerror", None) not in (5, 32, 33):
                        raise
                    sleep(delay)
        finally:
            file.close()
            temporary.unlink(missing_ok=True)


def _publish(path: Path, candidate: dict) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=5):
        try:
            previous = json.loads(path.read_bytes())
            through = date.fromisoformat(previous["through"]).isoformat()
            known_dates = {date.fromisoformat(bar["time"]).isoformat() for bar in previous["bars"]}
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            # A missing or malformed file has no usable snapshot to preserve.
            pass
        else:
            if candidate["through"] < through:
                raise ValueError(f"Download is older than the installed snapshot through {through}; cache unchanged.")
            dropped = sorted(known_dates - {bar["time"] for bar in candidate["bars"]})
            if dropped:
                raise ValueError(f"Download omits {len(dropped):,} cached sessions (first: {dropped[0]}); cache unchanged.")
        _write_snapshot(path, candidate)


def refresh(path: Path, now: datetime, *, download: Callable | None = None) -> dict:
    dates = _completed_dates(now)
    if not dates:
        raise ValueError("No completed SPY sessions in the requested range.")
    if download is None:
        import yfinance

        download = yfinance.download
    frame = download(
        "SPY", start=START.isoformat(), end=(dates[-1] + timedelta(days=1)).isoformat(),
        interval="1d", auto_adjust=False, actions=False, prepost=False, progress=False, threads=False,
    )
    snapshot = _snapshot(dates, _normalize(frame, set(dates)))
    _publish(path, snapshot)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()
    try:
        snapshot = refresh(args.cache, datetime.now(NEW_YORK))
    except (OSError, ValueError) as exc:
        print(f"SPY refresh failed: {exc}", file=sys.stderr)
        return 1
    bars = snapshot["bars"]
    missing = snapshot["missing_sessions"]
    print(f"SPY: {len(bars):,} completed daily bars, {bars[0]['time']} through {bars[-1]['time']}.")
    print(f"NYSE calendar through {snapshot['through']}: {len(missing):,} missing sessions.")
    if missing:
        print("Missing: " + ", ".join(missing))
    print(f"Cache: {args.cache}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
