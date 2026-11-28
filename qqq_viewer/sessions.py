from datetime import date, datetime
from functools import lru_cache

import pandas_market_calendars as markets

from equity_backtest.market_time import NEW_YORK, Session
START_DATE = date(2020, 1, 1)


@lru_cache(maxsize=16)
def _sessions(start: date, end: date) -> tuple[Session, ...]:
    if end < start:
        return ()
    schedule = markets.get_calendar("NASDAQ").schedule(start, end)
    return tuple(
        Session(index.date(), int(row.market_open.timestamp()), int(row.market_close.timestamp()))
        for index, row in schedule.iterrows()
    )


def sessions(start: date, end: date) -> list[Session]:
    return list(_sessions(start, end))


def completed_sessions(now: datetime) -> list[Session]:
    if now.utcoffset() is None:
        raise ValueError("A timezone-aware cutoff is required.")
    return [
        session for session in sessions(START_DATE, now.astimezone(NEW_YORK).date())
        if session.close_utc <= now.timestamp()
    ]
