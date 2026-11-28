"""Read and aggregate Sierra Chart .scid intraday records into five-minute SPY bars."""

import math
import struct
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .sessions import Session
from .spy_5m_store import STEP, FiveMinuteBar, expected_times, normalize_bar

SC_EPOCH = datetime(1899, 12, 30, tzinfo=timezone.utc)
SCID_MAGIC = b"SCID"
SCID_HEADER = struct.Struct("<4sIIHHI36s")
SCID_RECORD = struct.Struct("<q4f4I")
SCID_HEADER_SIZE = 56
SCID_RECORD_SIZE = 40
SCID_VERSION = 1
UNIX_OFFSET_US = 25569 * 86_400 * 1_000_000
CHUNK_RECORDS = 2_000_000


def _validate_header(stream, path: Path) -> int:
    header = stream.read(SCID_HEADER_SIZE)
    if len(header) != SCID_HEADER_SIZE:
        raise ValueError(f"Truncated SCID header in {path}.")
    magic, header_size, record_size, version, _, _, _ = SCID_HEADER.unpack(header)
    if (magic != SCID_MAGIC or header_size != SCID_HEADER_SIZE
            or record_size != SCID_RECORD_SIZE or version != SCID_VERSION):
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
        stream.seek(SCID_HEADER_SIZE + mid * SCID_RECORD_SIZE)
        dt_us = SCID_RECORD.unpack(stream.read(SCID_RECORD_SIZE))[0]
        if dt_us < target_us:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _extract_sample(open_: float, high: float, low: float, close: float,
                    num_trades: int, volume: int) -> tuple[float, float, float, float, int, float] | None:
    if not all(math.isfinite(value) for value in (open_, high, low, close)):
        return None
    if num_trades == 0 and open_ > 0:
        o, h, l, c = (round(value, 2) for value in (open_, high, low, close))
        return o, h, l, c, volume, (h + l + c) / 3.0
    if num_trades == 1 and close > 0 and (open_ == 0.0 or open_ < -1e30) and volume > 0:
        price = round(close, 4)
        return price, price, price, price, volume, price
    return None


def read_scid_five_minute_bars(scid_path: Path, window: list[Session]) -> dict[Session, list[FiveMinuteBar]]:
    """Read RTH records from a Sierra .scid file and aggregate onto the five-minute grid."""
    if not window:
        return {}
    ordered = sorted(window, key=lambda session: session.open_utc)
    owner = {timestamp: session for session in ordered for timestamp in expected_times(session)}
    grouped: dict[Session, list[FiveMinuteBar]] = {session: [] for session in window}
    buckets: dict[int, list[tuple[float, float, float, float, int, float]]] = defaultdict(list)

    start_us = ordered[0].open_utc * 1_000_000 + UNIX_OFFSET_US
    end_us = ordered[-1].close_utc * 1_000_000 + UNIX_OFFSET_US

    with scid_path.open("rb") as stream:
        count = _validate_header(stream, scid_path)
        first = _lower_bound(stream, count, start_us)
        last = _lower_bound(stream, count, end_us)
        stream.seek(SCID_HEADER_SIZE + first * SCID_RECORD_SIZE)
        remaining = last - first
        while remaining > 0:
            batch = min(remaining, CHUNK_RECORDS)
            raw = stream.read(batch * SCID_RECORD_SIZE)
            remaining -= batch
            for dt_us, open_, high, low, close, num_trades, volume, _, _ in SCID_RECORD.iter_unpack(raw):
                bucket = (dt_us - UNIX_OFFSET_US) // 1_000_000
                bucket -= bucket % STEP
                if bucket not in owner:
                    continue
                sample = _extract_sample(open_, high, low, close, num_trades, volume)
                if sample is not None:
                    buckets[bucket].append(sample)

    for bucket in sorted(buckets):
        records = buckets[bucket]
        total_volume = sum(record[4] for record in records)
        if total_volume > 0:
            wap = round(sum(record[5] * record[4] for record in records) / total_volume, 6)
        else:
            wap = round(sum(record[5] for record in records) / len(records), 6)
        bar = normalize_bar(FiveMinuteBar(
            bucket,
            records[0][0],
            max(record[1] for record in records),
            min(record[2] for record in records),
            records[-1][3],
            float(total_volume),
            wap,
        ))
        if bar is not None:
            grouped[owner[bucket]].append(bar)
    return grouped
