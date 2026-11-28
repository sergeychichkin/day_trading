"""US equity session timestamps."""

from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Session:
    date: date
    open_utc: int
    close_utc: int

    @property
    def minutes(self) -> range:
        return range(self.open_utc, self.close_utc, 60)
