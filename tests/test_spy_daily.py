import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

import pandas as pd
import pandas_market_calendars as markets
import pytest


@pytest.fixture
def refresh():
    def run_refresh(*args, **kwargs):
        try:
            from qqq_viewer.spy_daily import refresh
        except ModuleNotFoundError as exc:
            if exc.name != "qqq_viewer.spy_daily":
                raise
            pytest.fail("SPY refresh is not implemented", pytrace=False)

        return refresh(*args, **kwargs)

    return run_refresh


@pytest.fixture(scope="module")
def days():
    return markets.get_calendar("NYSE").schedule("2000-01-01", "2000-02-29").index


def prices(days, values=None):
    return pd.DataFrame(values or [(100, 104, 98, 101, 500)] * len(days), index=days,
                        columns=["Open", "High", "Low", "Close", "Volume"])


def run(refresh, tmp_path, frame, *, now=None):
    cache = tmp_path / "spy.json"
    snapshot = refresh(cache, now or datetime(2000, 3, 1, tzinfo=timezone.utc),
                       download=lambda *args, **kwargs: frame)
    assert json.loads(cache.read_text(encoding="utf-8")) == snapshot
    return snapshot


@pytest.mark.parametrize("today_range", [(201, 199), (270, 160)])
def test_distance_levels_ignore_today_range_and_roll_across_months(refresh, tmp_path, days, today_range):
    frame = prices(days)
    frame.iloc[0] = (100, 124, 88, 101, 500)
    frame.iloc[10] = (200, *today_range, 200, 700)
    bars = run(refresh, tmp_path, frame)["bars"]

    assert bars[9]["upper"] is None and bars[9]["lower"] is None
    assert bars[10]["upper"] == pytest.approx(212)
    assert bars[10]["lower"] == pytest.approx(194)
    assert bars[11]["upper"] == pytest.approx(103.6 + (today_range[0] - 200) / 20)
    assert bars[11]["lower"] == pytest.approx(98.2 - (200 - today_range[1]) / 20)
    february = next(bar for bar in bars if bar["time"] == "2000-02-01")
    assert february["upper"] is not None and february["lower"] is not None


def test_distance_percent_is_applied_to_todays_open(refresh, tmp_path, days):
    frame = prices(days)
    frame.iloc[10] = (200, 270, 160, 200, 700)
    bar = run(refresh, tmp_path, frame)["bars"][10]
    assert bar["upper"] == pytest.approx(208)
    assert bar["lower"] == pytest.approx(196)


def test_new_daily_snapshot_identifies_yahoo_spy(refresh, tmp_path, days):
    snapshot = run(refresh, tmp_path, prices(days[:10]))
    assert {key: snapshot[key] for key in ("schema", "ticker", "source")} == {
        "schema": "yahoo-daily-v1", "ticker": "SPY", "source": "Yahoo Finance",
    }


def test_sma_warmup_and_ema_slope_colors(refresh, tmp_path, days):
    values = [(v, v, v, v, v) for v in [*range(1, 22), 11.5, 1, 10.5]]
    bars = run(refresh, tmp_path, prices(days[:len(values)], values))["bars"]
    assert bars[18]["ema_20"] is None and bars[18]["volume_sma_20"] is None
    assert (bars[19]["ema_20"], bars[19]["ema_direction"], bars[19]["volume_sma_20"]) == (10.5, "neutral", 10.5)
    assert (bars[20]["ema_20"], bars[20]["ema_direction"], bars[20]["volume_sma_20"]) == (11.5, "up", 11.5)
    assert (bars[21]["ema_20"], bars[21]["ema_direction"]) == (11.5, "up")
    assert (bars[22]["ema_20"], bars[22]["ema_direction"]) == (10.5, "down")
    assert (bars[23]["ema_20"], bars[23]["ema_direction"]) == (10.5, "down")


def test_missing_session_restarts_all_indicator_windows(refresh, tmp_path):
    days = markets.get_calendar("NYSE").schedule("2000-01-01", "2000-03-15").index
    frame = prices(days[:20])
    later = prices(days[21:], [(200, 208, 196, 202, 750)] * len(days[21:]))
    snapshot = run(refresh, tmp_path, pd.concat([frame, later]),
                   now=datetime(2000, 3, 16, tzinfo=timezone.utc))
    assert snapshot["missing_sessions"] == [days[20].date().isoformat()]
    bars = snapshot["bars"]
    assert [bars[19][key] for key in ("upper", "lower", "ema_20", "volume_sma_20")] == [104, 98, 101, 500]
    assert all(bar["upper"] is None and bar["lower"] is None for bar in bars[20:30])
    assert (bars[30]["upper"], bars[30]["lower"]) == pytest.approx((208, 196))
    assert all(bar["ema_20"] is None and bar["volume_sma_20"] is None for bar in bars[20:39])
    assert (bars[39]["ema_20"], bars[39]["ema_direction"], bars[39]["volume_sma_20"]) == (202, "neutral", 750)


@pytest.mark.parametrize(("now", "through", "returned"), [
    ("2025-11-28T17:59:59+00:00", "2025-11-26", ["2025-11-26"]),
    ("2025-11-28T18:00:00+00:00", "2025-11-28", ["2025-11-26", "2025-11-28"]),
    ("2025-04-18T23:00:00+00:00", "2025-04-17", ["2025-04-17"]),
])
def test_nyse_holiday_and_exact_early_close(refresh, tmp_path, now, through, returned):
    frame = prices(pd.to_datetime(["2025-04-17", "2025-04-18"] if "04-18" in now else
                                  ["2025-11-26", "2025-11-27", "2025-11-28"]))
    snapshot = run(refresh, tmp_path, frame, now=datetime.fromisoformat(now))
    assert snapshot["through"] == through
    assert [bar["time"] for bar in snapshot["bars"]] == returned


@pytest.mark.parametrize("multiindex", [False, True])
def test_download_excludes_in_progress_session(refresh, tmp_path, days, multiindex):
    frame = prices(days[:3])
    if multiindex:
        frame.columns = pd.MultiIndex.from_product([frame.columns, ["SPY"]])

    def download(ticker, **options):
        assert ticker == "SPY"
        assert {key: options[key] for key in ("start", "end", "interval", "auto_adjust")} == {
            "start": "2000-01-01", "end": "2000-01-05", "interval": "1d", "auto_adjust": False,
        }
        return frame

    snapshot = refresh(tmp_path / "spy.json", datetime(2000, 1, 5, 16, tzinfo=timezone.utc),
                       download=download)
    assert snapshot["through"] == "2000-01-04"
    assert [bar["time"] for bar in snapshot["bars"]] == ["2000-01-03", "2000-01-04"]


@pytest.mark.parametrize("invalid", [None, (100, 80, 90, 101, 100)])
def test_invalid_refresh_preserves_snapshot(refresh, tmp_path, days, invalid):
    frame = prices(days[:1], [invalid]) if invalid else pd.DataFrame()
    cache = tmp_path / "spy.json"
    cache.write_bytes(b'{"previous": true}')
    with pytest.raises(ValueError):
        refresh(cache, datetime(2000, 1, 4, tzinfo=timezone.utc), download=lambda *a, **kw: frame)
    assert cache.read_bytes() == b'{"previous": true}'


def test_partial_refresh_cannot_remove_cached_sessions(refresh, tmp_path, days):
    now = datetime(2000, 1, 6, 22, tzinfo=timezone.utc)
    cache = tmp_path / "spy.json"
    first = refresh(cache, now, download=lambda *a, **kw: prices(days[:3]))
    assert first["missing_sessions"] == ["2000-01-06"]
    with pytest.raises(ValueError, match=r"omit.*2000-01-04"):
        refresh(cache, now, download=lambda *a, **kw: prices(days[[0, 2, 3]]))
    assert json.loads(cache.read_text(encoding="utf-8")) == first


def test_overlapping_older_refresh_cannot_rollback_cutoff(refresh, tmp_path, days):
    cache = tmp_path / "spy.json"
    waiting, release = Event(), Event()

    def older_download(*args, **kwargs):
        waiting.set()
        assert release.wait(timeout=10)
        return prices(days[:2])

    with ThreadPoolExecutor(max_workers=1) as workers:
        old = workers.submit(refresh, cache, datetime(2000, 1, 4, 22, tzinfo=timezone.utc),
                             download=older_download)
        try:
            assert waiting.wait(timeout=10)
            latest = refresh(cache, datetime(2000, 1, 5, 22, tzinfo=timezone.utc),
                             download=lambda *a, **kw: prices(days[:2]))
        finally:
            release.set()
        with pytest.raises(ValueError, match=r"older.*2000-01-05"):
            old.result(timeout=10)
    assert json.loads(cache.read_text(encoding="utf-8")) == latest


@pytest.mark.parametrize("persistent", [False, True])
def test_windows_replacement_contention_preserves_snapshot_or_recovers(
        refresh, tmp_path, days, monkeypatch, persistent):
    cache = tmp_path / "spy.json"
    first = refresh(cache, datetime(2000, 1, 4, 22, tzinfo=timezone.utc),
                    download=lambda *a, **kw: prices(days[:2]))
    old_bytes = cache.read_bytes()
    original_replace = Path.replace
    attempts = []

    def replace(source, target):
        attempts.append(source)
        if persistent or len(attempts) == 1:
            error = PermissionError("destination is open")
            error.winerror = 32
            raise error
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", replace)
    now = datetime(2000, 1, 5, 22, tzinfo=timezone.utc)
    download = lambda *a, **kw: prices(days[:3])
    if persistent:
        with pytest.raises(PermissionError, match="destination is open"):
            refresh(cache, now, download=download)
        assert cache.read_bytes() == old_bytes
    else:
        snapshot = refresh(cache, now, download=download)
        assert snapshot["through"] > first["through"]
        assert json.loads(cache.read_text(encoding="utf-8")) == snapshot
    assert 2 <= len(attempts) <= 6
    assert not any(path.exists() for path in attempts)
