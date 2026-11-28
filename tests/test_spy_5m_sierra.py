import math
import struct
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from qqq_viewer.sessions import NEW_YORK
from qqq_viewer.spy_5m_sierra import SC_EPOCH, SCID_RECORD
from qqq_viewer.spy_5m_store import FiveMinuteBar, sessions


def _write_scid(path: Path, records: list[tuple[datetime, float, float, float, float, int, int, int, int]],
                *, magic: bytes = b"SCID", hdr_size: int = 56, rec_size: int = 40, version: int = 1,
                trailing: bytes = b"") -> None:
    header = struct.pack("<4sIIHHI36s", magic, hdr_size, rec_size, version, 0, 0, b"\x00" * 36)
    body = bytearray()
    for dt, o, h, l, c, trades, vol, bvol, avol in records:
        dt_us = round((dt.astimezone(timezone.utc) - SC_EPOCH).total_seconds() * 1_000_000)
        body.extend(SCID_RECORD.pack(dt_us, o, h, l, c, trades, vol, bvol, avol))
    path.write_bytes(header + bytes(body) + trailing)


def test_rejects_invalid_scid_header_and_truncated_file(tmp_path):
    from qqq_viewer.spy_5m_sierra import read_scid_five_minute_bars

    win = sessions(date(2010, 1, 4), date(2010, 1, 4))
    short = tmp_path / "short.scid"
    short.write_bytes(b"SCID" * 5)
    with pytest.raises(ValueError, match="SCID"):
        read_scid_five_minute_bars(short, win)

    for name, kwargs in [
        ("bad_magic.scid", {"magic": b"NOPE"}),
        ("bad_hdr.scid", {"hdr_size": 64}),
        ("bad_rec.scid", {"rec_size": 32}),
        ("bad_ver.scid", {"version": 2}),
        ("truncated.scid", {"trailing": b"\x00" * 7}),
    ]:
        p = tmp_path / name
        _write_scid(p, [], **kwargs)
        with pytest.raises(ValueError, match="SCID"):
            read_scid_five_minute_bars(p, win)


def test_aggregates_1m_records_into_five_minute_bars_with_typical_wap(tmp_path):
    from qqq_viewer.spy_5m_sierra import read_scid_five_minute_bars

    def ny(y, m, d, hh, mm):
        return datetime(y, m, d, hh, mm, tzinfo=NEW_YORK)

    scid = tmp_path / "SPY.scid"
    _write_scid(scid, [
        (ny(2010, 1, 4, 9, 29), 112.00, 112.10, 111.90, 112.05, 0, 500, 0, 0),
        (ny(2010, 1, 4, 9, 30), 112.35, 112.50, 112.30, 112.40, 0, 100, 0, 0),
        (ny(2010, 1, 4, 9, 31), 112.40, 112.60, 112.35, 112.55, 0, 200, 0, 0),
        (ny(2010, 1, 4, 9, 32), 112.55, 112.55, 112.55, 112.55, 0, 0, 0, 0),
        (ny(2010, 1, 4, 16, 0), 113.00, 113.10, 112.90, 113.05, 0, 400, 0, 0),
        (ny(2010, 1, 5, 9, 30), 113.10, 113.20, 113.00, 113.15, 0, 0, 0, 0),
    ])

    win = sessions(date(2010, 1, 4), date(2010, 1, 5))
    grouped = read_scid_five_minute_bars(scid, win)

    tp1 = (112.50 + 112.30 + 112.40) / 3.0
    tp2 = (112.60 + 112.35 + 112.55) / 3.0
    expected_wap_day1 = round((tp1 * 100 + tp2 * 200) / 300, 6)

    assert grouped[win[0]] == [
        FiveMinuteBar(int(ny(2010, 1, 4, 9, 30).timestamp()), 112.35, 112.6, 112.3, 112.55, 300.0, expected_wap_day1),
    ]
    assert grouped[win[1]] == [
        FiveMinuteBar(int(ny(2010, 1, 5, 9, 30).timestamp()), 113.1, 113.2, 113.0, 113.15, 0.0, None),
    ]


def test_handles_tick_and_quote_only_records_and_empty_window(tmp_path):
    from qqq_viewer.spy_5m_sierra import read_scid_five_minute_bars

    def ny(y, m, d, hh, mm, ss):
        return datetime(y, m, d, hh, mm, ss, tzinfo=NEW_YORK)

    scid = tmp_path / "SPY_ticks.scid"
    odd_lot_flag = -1.99900285e37
    _write_scid(scid, [
        (ny(2025, 11, 25, 9, 30, 0), 0.0, 600.05, 600.00, 600.01, 0, 0, 0, 0),
        (ny(2025, 11, 25, 9, 30, 1), 0.0, 600.05, 600.00, 600.00, 1, 100, 100, 0),
        (ny(2025, 11, 25, 9, 30, 2), odd_lot_flag, 600.10, 600.00, 600.3125, 1, 300, 0, 300),
    ])

    assert read_scid_five_minute_bars(scid, []) == {}

    win = sessions(date(2025, 11, 25), date(2025, 11, 25))
    grouped = read_scid_five_minute_bars(scid, win)
    expected_wap = round((600.0 * 100 + 600.3125 * 300) / 400, 6)
    assert grouped[win[0]] == [
        FiveMinuteBar(int(ny(2025, 11, 25, 9, 30, 0).timestamp()), 600.0, 600.3125, 600.0, 600.3125, 400.0, expected_wap),
    ]


def test_rejects_non_monotonic_and_filters_corrupt_1m_ohlc(tmp_path):
    from qqq_viewer.spy_5m_sierra import read_scid_five_minute_bars

    def ny(hh, mm):
        return datetime(2010, 1, 4, hh, mm, tzinfo=NEW_YORK)

    win = sessions(date(2010, 1, 4), date(2010, 1, 4))
    non_mono = tmp_path / "non_mono.scid"
    _write_scid(non_mono, [
        (ny(9, 31), 112.0, 112.1, 111.9, 112.05, 0, 100, 0, 0),
        (ny(9, 30), 112.0, 112.1, 111.9, 112.05, 0, 100, 0, 0),
    ])
    with pytest.raises(ValueError, match="monotonic"):
        read_scid_five_minute_bars(non_mono, win)

    corrupt = tmp_path / "corrupt.scid"
    _write_scid(corrupt, [
        (ny(9, 30), 112.0, 112.2, 111.8, 112.1, 0, 100, 0, 0),
        (ny(9, 31), 112.1, 111.0, 112.5, 112.1, 0, 9999, 0, 0),  # inverted high < low
        (ny(9, 32), 112.1, 112.3, 0.0, 112.1, 0, 9999, 0, 0),    # low <= 0
        (ny(9, 33), 112.1, math.nan, 112.0, 112.1, 0, 9999, 0, 0),
    ])
    grouped = read_scid_five_minute_bars(corrupt, win)
    tp = (112.2 + 111.8 + 112.1) / 3.0
    assert grouped[win[0]] == [
        FiveMinuteBar(int(ny(9, 30).timestamp()), 112.0, 112.2, 111.8, 112.1, 100.0, round(tp, 6)),
    ]


def test_supports_positive_num_trades_bars_1m_completeness_dst_and_sparse_window(tmp_path):
    from qqq_viewer.spy_5m_sierra import read_scid_five_minute_details

    est_day, = sessions(date(2010, 3, 12), date(2010, 3, 12))
    edt_day, = sessions(date(2010, 3, 15), date(2010, 3, 15))
    early_day, = sessions(date(2025, 11, 28), date(2025, 11, 28))

    records = []
    # Full 1m EST session with num_trades > 0
    for ts in range(est_day.open_utc, est_day.close_utc, 60):
        records.append((datetime.fromtimestamp(ts, timezone.utc), 115.0, 115.2, 114.9, 115.1, 7, 100, 0, 0))
    # Partial 1m EDT session: skip one constituent minute (09:32) so 5m slots still exist, but 1m is incomplete
    for ts in range(edt_day.open_utc, edt_day.close_utc, 60):
        if ts == edt_day.open_utc + 120:
            continue
        records.append((datetime.fromtimestamp(ts, timezone.utc), 115.5, 115.7, 115.4, 115.6, 5, 100, 0, 0))
    # Early close 2025-11-28 (42 bars) using tick records
    for ts in range(early_day.open_utc, early_day.close_utc, 300):
        records.append((datetime.fromtimestamp(ts, timezone.utc), 0.0, 600.1, 600.0, 600.05, 1, 50, 0, 50))

    scid = tmp_path / "mixed.scid"
    _write_scid(scid, records)
    details = read_scid_five_minute_details(scid, [est_day, early_day, edt_day])

    est_bars, est_complete = details[est_day]
    edt_bars, edt_complete = details[edt_day]
    early_bars, early_complete = details[early_day]
    assert len(est_bars) == 78 and est_complete is True
    assert len(edt_bars) == 78 and edt_complete is False
    assert len(early_bars) == 42 and early_complete is True
