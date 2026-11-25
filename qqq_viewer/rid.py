"""Session-local RID bands shared by the replay and report chart."""

from collections.abc import Sequence
from dataclasses import dataclass
from sys import float_info

from .store import Bar

MAX_RID_BARS = 25


@dataclass(frozen=True)
class RidSegment:
    first: int
    last: int
    upper: float
    lower: float


@dataclass(frozen=True)
class RidSession:
    prior_upper: tuple[float | None, ...]
    prior_length: tuple[int, ...]
    segments: tuple[RidSegment, ...]


def calculate_rid(bars: Sequence[Bar]) -> RidSession:
    prior_upper = []
    prior_length = []
    segments = []
    length = 0
    for bar in bars:
        prior_upper.append(upper if length else None)
        prior_length.append(length)
        tolerance = 0.01 + abs(bar.close) * float_info.epsilon
        if length and length < MAX_RID_BARS and lower + tolerance < bar.close < upper - tolerance:
            length += 1
        else:
            if length:
                segments.append(RidSegment(first, last, upper, lower))
            first, upper, lower = bar.time, bar.high, bar.low
            length = 1
        last = bar.time
    if length:
        segments.append(RidSegment(first, last, upper, lower))
    return RidSession(tuple(prior_upper), tuple(prior_length), tuple(segments))
