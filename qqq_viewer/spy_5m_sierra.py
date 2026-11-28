"""Read and aggregate Sierra Chart .scid intraday records into five-minute SPY bars."""

import math
import struct
from datetime import date, datetime, timezone
from pathlib import Path

from .sessions import NEW_YORK, Session
from .spy_5m_store import STEP, FiveMinuteBar, expected_times, normalize_bar

SC_EPOCH = datetime(1899, 12, 30, tzinfo=timezone.utc)
SCID_MAGIC = b"SCID"
SCID_HEADER = struct.Struct("<4sIIHHI36s")
SCID_RECORD = struct.Struct("<q4f4I")
SCID_VERSION = 1
UNIX_OFFSET_US = int((datetime(1970, 1, 1, tzinfo=timezone.utc) - SC_EPOCH).total_seconds()) * 1_000_000
CHUNK_RECORDS = 2_000_000
SPARSE_GAP_SECONDS = 7 * 86_400


def _validate_header(stream, path: Path) -> int:
    header = stream.read(SCID_HEADER.size)
    if len(header) != SCID_HEADER.size:
        raise ValueError(f"Truncated SCID header in {path}.")
    magic, header_size, record_size, version, _, _, _ = SCID_HEADER.unpack(header)
    if (magic != SCID_MAGIC or header_size != SCID_HEADER.size
            or record_size != SCID_RECORD.size or version != SCID_VERSION):
        raise ValueError(f"Unsupported SCID layout in {path}.")
    stream.seek(0, 2)
    payload_bytes = stream.tell() - header_size
    if payload_bytes < 0 or payload_bytes % record_size:
        raise ValueError(f"Truncated SCID record payload in {path}.")
    return payload_bytes // record_size


def _lower_bound(stream, count: int, target_us: int) -> int:
    lo, hi = 0, count
    while lo < hi:
        mid = (lo + hi) // 2
        stream.seek(SCID_HEADER.size + mid * SCID_RECORD.size)
        dt_us = SCID_RECORD.unpack(stream.read(SCID_RECORD.size))[0]
        if dt_us < target_us:
            lo = mid + 1
        else:
            hi = mid
    return lo


def scid_identity(scid_path: Path) -> tuple[int, int, int, int]:
    """Validate SCID layout and return (record_count, file_bytes, first_dt_us, last_dt_us)."""
    with scid_path.open("rb") as stream:
        count = _validate_header(stream, scid_path)
        stream.seek(0, 2)
        size = stream.tell()
        if count == 0:
            return 0, size, 0, 0
        stream.seek(SCID_HEADER.size)
        first_dt_us = SCID_RECORD.unpack(stream.read(SCID_RECORD.size))[0]
        stream.seek(SCID_HEADER.size + (count - 1) * SCID_RECORD.size)
        last_dt_us = SCID_RECORD.unpack(stream.read(SCID_RECORD.size))[0]
    return count, size, first_dt_us, last_dt_us


def scid_first_date(scid_path: Path) -> date:
    """Return the New York trading date of the first record in a non-empty .scid file."""
    count, _, first_dt_us, _ = scid_identity(scid_path)
    if count == 0:
        raise ValueError(f"Sierra SCID file is empty: {scid_path}")
    first_ts = (first_dt_us - UNIX_OFFSET_US) // 1_000_000
    return datetime.fromtimestamp(first_ts, NEW_YORK).date()


def _extract_sample(open_: float, high: float, low: float, close: float,
                    num_trades: int, volume: int) -> tuple[bool, float, float, float, float, int, float] | None:
    if not all(math.isfinite(value) for value in (open_, high, low, close)):
        return None
    if open_ > 0:
        if not (0 < low <= min(open_, close) <= max(open_, close) <= high and volume >= 0):
            return None
        o, h, l, c = (round(value, 2) for value in (open_, high, low, close))
        if not (0 < l <= min(o, c) <= max(o, c) <= h):
            return None
        return True, o, h, l, c, volume, (h + l + c) / 3.0
    if num_trades == 1 and close > 0 and open_ <= 0 and volume > 0:
        price = round(close, 4)
        return False, price, price, price, price, volume, price
    return None


def _contiguous_spans(ordered: list[Session]) -> list[tuple[int, int]]:
    spans = []
    start = ordered[0].open_utc
    end = ordered[0].close_utc
    for session in ordered[1:]:
        if session.open_utc - end > SPARSE_GAP_SECONDS:
            spans.append((start, end))
            start = session.open_utc
        end = session.close_utc
    spans.append((start, end))
    return spans


def read_scid_five_minute_details(
    scid_path: Path, window: list[Session],
) -> dict[Session, tuple[list[FiveMinuteBar], bool]]:
    """Aggregate RTH .scid records into five-minute bars and check 1-minute bar completeness."""
    if not window:
        return {}
    ordered = sorted(window, key=lambda session: session.open_utc)
    owner = {timestamp: session for session in ordered for timestamp in expected_times(session)}
    # Per-bucket O(1) accumulator: [open, high, low, close, volume, weighted_tp]
    accum: dict[int, list[float]] = {}
    session_bars_seen: dict[Session, bool] = {session: False for session in ordered}
    session_minutes: dict[Session, set[int]] = {session: set() for session in ordered}

    with scid_path.open("rb") as stream:
        count = _validate_header(stream, scid_path)
        for span_start, span_end in _contiguous_spans(ordered):
            first = _lower_bound(stream, count, span_start * 1_000_000 + UNIX_OFFSET_US)
            last = _lower_bound(stream, count, span_end * 1_000_000 + UNIX_OFFSET_US)
            stream.seek(SCID_HEADER.size + first * SCID_RECORD.size)
            remaining = last - first
            prev_dt_us = -1
            while remaining > 0:
                batch = min(remaining, CHUNK_RECORDS)
                raw = stream.read(batch * SCID_RECORD.size)
                remaining -= batch
                for dt_us, open_, high, low, close, num_trades, volume, _, _ in SCID_RECORD.iter_unpack(raw):
                    if prev_dt_us != -1 and dt_us < prev_dt_us:
                        raise ValueError(f"Non-monotonic SCID timestamp in {scid_path}.")
                    prev_dt_us = dt_us
                    ts = (dt_us - UNIX_OFFSET_US) // 1_000_000
                    bucket = ts - (ts % STEP)
                    session = owner.get(bucket)
                    if session is None:
                        continue
                    sample = _extract_sample(open_, high, low, close, num_trades, volume)
                    if sample is None:
                        continue
                    is_bar, o, h, l, c, vol, tp = sample
                    if is_bar:
                        session_bars_seen[session] = True
                        session_minutes[session].add(ts - (ts % 60))
                    state = accum.get(bucket)
                    if state is None:
                        accum[bucket] = [o, h, l, c, float(vol), tp * vol]
                    else:
                        if h > state[1]:
                            state[1] = h
                        if l < state[2]:
                            state[2] = l
                        state[3] = c
                        state[4] += vol
                        state[5] += tp * vol

    grouped: dict[Session, list[FiveMinuteBar]] = {session: [] for session in window}
    for bucket in sorted(accum):
        o, h, l, c, total_volume, weighted_tp = accum[bucket]
        wap = round(weighted_tp / total_volume, 6) if total_volume > 0 else None
        bar = normalize_bar(FiveMinuteBar(bucket, o, h, l, c, total_volume, wap))
        if bar is not None:
            grouped[owner[bucket]].append(bar)

    result: dict[Session, tuple[list[FiveMinuteBar], bool]] = {}
    for session in window:
        bars = grouped[session]
        if session_bars_seen[session]:
            complete_1m = len(session_minutes[session]) == (session.close_utc - session.open_utc) // 60
        else:
            complete_1m = bool(bars)
        result[session] = (bars, complete_1m)
    return result


def read_scid_five_minute_bars(scid_path: Path, window: list[Session]) -> dict[Session, list[FiveMinuteBar]]:
    """Read RTH records from a Sierra .scid file and aggregate onto the five-minute grid."""
    return {session: bars for session, (bars, _) in read_scid_five_minute_details(scid_path, window).items()}
