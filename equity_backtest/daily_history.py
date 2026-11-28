"""Capture Yahoo daily responses before deriving replay inputs."""

import math
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from io import StringIO

import pandas as pd

from qqq_viewer.spy_daily import OHLCV, _snapshot


def download_daily(ticker, start, through):
    import yfinance as yf

    options = dict(start=start.isoformat(), end=(through + timedelta(days=1)).isoformat(),
                   interval="1d", auto_adjust=False, actions=True, prepost=False, raise_errors=True)
    result = {"params": options, "version": yf.__version__, "received_at": datetime.now(timezone.utc).isoformat()}
    try:
        frame = yf.Ticker(ticker).history(**options)
        result.update(status="complete" if not frame.empty else "empty", csv=frame.to_csv())
    except Exception as error:
        result.update(status="provider_error", detail=f"{type(error).__name__}: {error}")
    return result


def daily_snapshot(ticker, dates, response):
    expected = set(dates)
    rows, flags = {}, []
    if response.get("status") == "complete":
        frame = pd.read_csv(StringIO(response["csv"]), index_col=0)
        identifiers = []
        for label in frame.index:
            try:
                identifiers.append(date.fromisoformat(str(label)[:10]))
            except ValueError:
                identifiers.append(None)
        counts = Counter(identifiers)
        for index, ((timestamp, bar), day) in enumerate(zip(frame.iterrows(), identifiers, strict=True)):
            if day not in expected or counts[day] > 1:
                flags.append((str(timestamp), "daily_duplicate" if day in expected else "daily_off_calendar", index))
                continue
            try:
                o, h, l, c, v = (float(bar[column]) for column in OHLCV)
                valid = all(map(math.isfinite, (o, h, l, c, v))) and 0 < l <= min(o, c) <= max(o, c) <= h and v >= 0
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                flags.append((str(day), "invalid_daily_bar", index))
                continue
            rows[day] = (o, h, l, c, v)
            for column in ("Dividends", "Stock Splits", "Capital Gains"):
                if column in bar and pd.notna(bar[column]) and bar[column] != 0:
                    flags.append((str(day), column, f"row {index}: {bar[column]}"))
    return _snapshot(dates, rows, ticker), flags
