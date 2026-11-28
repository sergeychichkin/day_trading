import struct
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from qqq_viewer.sessions import NEW_YORK
from qqq_viewer.spy_5m_store import FiveMinuteBar, sessions

SC_EPOCH = datetime(1899, 12, 30, tzinfo=timezone.utc)
REC = struct.Struct("<q4f4I")


def _write_scid(path: Path, records: list[tuple[datetime, float, float, float, float, int, int, int, int]],
                *, magic: bytes = b"SCID", hdr_size: int = 56, rec_size: int = 40, version: int = 1,
                trailing: bytes = b"") -> None:
    header = struct.pack("<4sIIHHI36s", magic, hdr_size, rec_size, version, 0, 0, b"\x00" * 36)
    body = bytearray()
    for dt, o, h, l, c, trades, vol, bvol, avol in records:
        dt_us = round((dt.astimezone(timezone.utc) - SC_EPOCH).total_seconds() * 1_000_000)
        body.extend(REC.pack(dt_us, o, h, l, c, trades, vol, bvol, avol))
    path.write_bytes(header + bytes(body) + trailing)


def test_rejects_invalid_scid_header_and_truncated_file(tmp_path):
    from qqq_viewer.spy_5m_sierra import read_scid_five_minute_bars

    win = sessions(date(2010, 1, 4), date(2010, 1, 4))
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
    expected_wap_day2 = round((113.20 + 113.00 + 113.15) / 3.0, 6)

    assert grouped[win[0]] == [
        FiveMinuteBar(int(ny(2010, 1, 4, 9, 30).timestamp()), 112.35, 112.6, 112.3, 112.55, 300.0, expected_wap_day1),
    ]
    assert grouped[win[1]] == [
        FiveMinuteBar(int(ny(2010, 1, 5, 9, 30).timestamp()), 113.1, 113.2, 113.0, 113.15, 0.0, expected_wap_day2),
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
        (ny(2025, 11, 25, 9, 30, 2), odd_lot_flag, 600.10, 600.00, 600.30, 1, 300, 0, 300),
    ])

    assert read_scid_five_minute_bars(scid, []) == {}

    win = sessions(date(2025, 11, 25), date(2025, 11, 25))
    grouped = read_scid_five_minute_bars(scid, win)
    assert grouped[win[0]] == [
        FiveMinuteBar(int(ny(2025, 11, 25, 9, 30, 0).timestamp()), 600.0, 600.3, 600.0, 600.3, 400.0, 600.225),
    ]
