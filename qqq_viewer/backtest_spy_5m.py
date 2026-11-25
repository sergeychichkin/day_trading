"""Replay SPY five-minute Entry 1 against verified local history."""

import argparse
import hashlib
import json
import math
import shutil
import sqlite3
import struct
import sys
import tomllib
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from .opening_range import WilderATR
from .sessions import NEW_YORK
from .spy_5m_replay import AccountSettings, EntrySettings, replay_day
from .spy_5m_store import BAR_FIELDS, FiveMinuteBar, FiveMinuteStore, expected_times, normalize_bar, sessions

SPY_CONTRACT = "756733"
BAR_FINGERPRINT = struct.Struct("!q6d?")
LABELS = {"same_day_close": "Same-day close", "next_session_open": "Next-session open"}


@dataclass(frozen=True)
class BacktestSettings:
    ticker: str
    intraday_cache: Path
    daily_cache: Path
    entry: EntrySettings
    account: AccountSettings
    same_day_close: bool
    next_session_open: bool


def _table(config, name, allowed):
    section = config.get(name, {})
    if not isinstance(section, dict) or set(section) - set(allowed):
        raise ValueError(f"Unexpected settings in [{name}].")
    return section.copy()


def _numeric(value, key):
    if type(value) not in (int, float):
        raise ValueError(f"{key} must be a finite number.")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{key} must be a finite number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{key} must be a finite number.")
    return number


def _settings_entry(values):
    for key in ("entry1_start_time", "entry1_end_time"):
        if key in values:
            try:
                values[key] = time.fromisoformat(values[key])
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{key} must be a five-minute New York candle time.") from exc
    for key in ("entry_atr_buffer", "stop_atr_buffer"):
        if key in values:
            values[key] = _numeric(values[key], key)
            if values[key] < 0:
                raise ValueError(f"{key} cannot be negative.")
    entry = EntrySettings(**values)
    start, end = entry.entry1_start_time, entry.entry1_end_time
    if any(t.tzinfo is not None or t.second or t.microsecond or t.minute % 5 for t in (start, end)):
        raise ValueError("Use five-minute New York candle times without seconds or a time-zone offset.")
    if not time(9, 30) <= start <= end <= time(15, 55):
        raise ValueError("Entry window must be ordered and inside the 09:30–15:55 regular candle starts.")
    minutes = entry.entry_order_valid_minutes
    if type(minutes) is not int or minutes <= 0 or minutes % 5:
        raise ValueError("Order lifetime must be a positive multiple of five minutes.")
    return entry


def load_settings(path: Path, *, entry1_start_time: str | None = None,
                  entry1_end_time: str | None = None) -> BacktestSettings:
    with path.open("rb") as file:
        data = tomllib.load(file)
    if set(data) - {"ticker", "intraday_cache", "daily_cache", "entry1", "account", "exits"}:
        raise ValueError("SPY settings contain an unknown top-level key.")
    if data.get("ticker") != "SPY":
        raise ValueError("The five-minute runner currently supports only a verified SPY cache.")
    inputs = []
    for key in ("intraday_cache", "daily_cache"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"Set {key} to an input path.")
        inputs.append((path.parent / data[key]).resolve())
    values = _table(data, "entry1", [field.name for field in fields(EntrySettings)])
    for key, override in (("entry1_start_time", entry1_start_time), ("entry1_end_time", entry1_end_time)):
        if override is not None:
            values[key] = override
    entry = _settings_entry(values)
    account = AccountSettings(**{key: _numeric(value, key)
                                for key, value in _table(data, "account", [f.name for f in fields(AccountSettings)]).items()})
    if account.starting_balance <= 0 or account.tick_size <= 0 or not 0 < account.risk_pct <= 100:
        raise ValueError("starting_balance, tick_size, and risk_pct must be positive; risk_pct cannot exceed 100.")
    if not 0 < account.cash_cap <= 1:
        raise ValueError("cash_cap must be greater than zero and at most 1.")
    if not 0 <= account.slippage_bps < 10_000 or account.slippage_floor < 0:
        raise ValueError("Slippage must be nonnegative and slippage_bps below 10,000.")
    exits = dict(same_day_close=True, next_session_open=False)
    exits.update(_table(data, "exits", exits))
    if any(type(value) is not bool for value in exits.values()) or not any(exits.values()):
        raise ValueError("Enable at least one exit with boolean settings.")
    return BacktestSettings("SPY", *inputs, entry, account, **exits)


def _positive(value):
    try:
        number = _numeric(value, "price")
    except ValueError:
        return None
    return number if number > 0 else None


def _daily_snapshot(path):
    raw = path.read_bytes()
    snapshot = json.loads(raw)
    rows = snapshot.get("bars") if isinstance(snapshot, dict) else None
    if not isinstance(rows, list):
        raise ValueError("SPY daily snapshot has no bars list.")
    daily = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(day := row.get("time"), str):
            raise ValueError("SPY daily snapshot has an invalid date row.")
        if day in daily:
            raise ValueError(f"Duplicate SPY daily snapshot date: {day}.")
        daily[day] = row
    return daily, hashlib.sha256(raw).hexdigest()


def _levels(row, opening):
    if row is None:
        return None, None, "daily_upper_unavailable"
    daily_open = _positive(row.get("open"))
    if daily_open is None:
        return None, None, "daily_open_unavailable"
    if abs(Decimal(str(opening)) - Decimal(str(daily_open))) > Decimal(str(daily_open)) * Decimal("0.05"):
        return None, None, "opening_price_mismatch"
    upper, lower = _positive(row.get("upper")), _positive(row.get("lower"))
    if lower is not None and not 0.5 <= lower / daily_open <= 1:
        lower = None
    if upper is None or not 1 <= upper / daily_open <= 2:
        return None, lower, "daily_upper_unavailable"
    return upper, lower, "available" if lower is not None else "daily_lower_unavailable"


def _verified_bars(db, session):
    day = session.date.isoformat()
    expected = list(expected_times(session))
    rows = db.execute(f"SELECT {BAR_FIELDS} FROM bars WHERE date=? ORDER BY time", (day,)).fetchall()
    if [row[0] for row in rows] != expected:
        raise ValueError(f"Incomplete SPY five-minute grid on {day}.")
    if db.execute("SELECT 1 FROM conflicts WHERE date=? LIMIT 1", (day,)).fetchone():
        raise ValueError(f"Conflicting SPY five-minute bars on {day}.")
    if not db.execute("SELECT 1 FROM sessions WHERE date=? AND verified=1 AND completed=1", (day,)).fetchone() or not db.execute(
        "SELECT 1 FROM attempts WHERE date=? AND status='complete' AND accepted=? LIMIT 1", (day, len(expected)),
    ).fetchone():
        raise ValueError(f"Unverified SPY session on {day}.")
    bars = [FiveMinuteBar(*row) for row in rows]
    try:
        usable = all(normalize_bar(bar) == bar and (bar.volume == 0 or bar.wap is not None) for bar in bars)
        usable = usable and any(bar.volume > 0 for bar in bars)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Malformed SPY five-minute bar on {day}.") from exc
    if not usable:
        raise ValueError(f"Invalid SPY bar or VWAP input on {day}.")
    return bars


def _file_stamp(path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@contextmanager
def _input_reader(path: Path):
    """Replay a stable local copy; SQLite may create WAL sidecars even with mode=ro."""
    wal, journal = (Path(str(path) + suffix) for suffix in ("-wal", "-journal"))
    sources = (path, wal, journal)
    before = tuple(map(_file_stamp, sources))
    if before[0] is None:
        raise ValueError(f"SPY intraday cache not found: {path}")
    if before[2] is not None and before[2][2]:
        raise ValueError("SPY intraday cache has an active rollback journal; wait for the writer and retry.")
    with TemporaryDirectory(prefix="spy-backtest-") as directory:
        copied = Path(directory) / "intraday.sqlite3"
        try:
            shutil.copyfile(path, copied)
            if before[1] is not None and before[1][2]:
                shutil.copyfile(wal, Path(str(copied) + "-wal"))
        except FileNotFoundError as exc:
            raise ValueError("SPY intraday cache changed during snapshot; retry when download is idle.") from exc
        if tuple(map(_file_stamp, sources)) != before:
            raise ValueError("SPY intraday cache changed during snapshot; retry when download is idle.")
        with FiveMinuteStore(copied, read_only=True)._connection() as db:
            yield db


def _point(points, timestamp, balance):
    record = {"time": timestamp, "value": balance}
    if points and points[-1]["time"] == timestamp:
        points[-1] = record
    else:
        points.append(record)


def _stats(case, starting):
    trades = case["trades"]
    values = [point["value"] for point in case["equity"]]
    ending = values[-1] if values else starting
    peak, drawdown = starting, 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = max(drawdown, 100 * (peak - value) / peak)
    return {
        "trades": len(trades), "ending_equity": ending, "net_pnl": ending - starting,
        "return_pct": 100 * (ending / starting - 1), "max_drawdown_pct": drawdown,
        "win_rate": 100 * sum(trade["pnl"] > 0 for trade in trades) / len(trades) if trades else None,
        "ambiguous": sum(trade["ambiguous"] for trade in trades),
        **{key: sum(trade[key] for trade in trades) for key in ("commission", "slippage", "gap_slippage")},
    }


def _chart_bars(bars):
    weighted = volume = 0.0
    rows = []
    for bar in bars:
        if bar.volume:
            weighted += bar.wap * bar.volume
            volume += bar.volume
        rows.append({**asdict(bar), "vwap": weighted / volume if volume else None})
    return rows


def _wire(settings):
    data = asdict(settings)
    for key in ("intraday_cache", "daily_cache"):
        data[key] = str(data[key])
    for key in ("entry1_start_time", "entry1_end_time"):
        data["entry"][key] = data["entry"][key].isoformat(timespec="minutes")
    return data


def run(settings: BacktestSettings, start: date | None = None, end: date | None = None) -> dict:
    if settings.ticker != "SPY":
        raise ValueError("The five-minute runner supports only SPY.")
    daily, daily_digest = _daily_snapshot(settings.daily_cache)
    modes = [key for key in LABELS if getattr(settings, key)]
    if not modes:
        raise ValueError("Enable at least one exit.")
    cases = [{"id": mode, "label": LABELS[mode], "trades": [], "events": [], "equity": [], "skips": Counter()}
             for mode in modes]
    initial = settings.account.starting_balance
    balances = dict.fromkeys(modes, initial)
    months, benchmark = {}, []
    digest = hashlib.sha256()
    atr = WilderATR(20)
    benchmark_open = None
    warmup_bars = signal_bars = 0
    tail = None
    tail_bars = None
    tail_error = None
    with _input_reader(settings.intraday_cache) as db:
        metadata = dict(db.execute("SELECT key,value FROM metadata"))
        if metadata.get("contract") != SPY_CONTRACT or metadata.get("volume_unit") not in {"shares", "lots"}:
            raise ValueError("The intraday cache does not identify the expected SPY contract and volume unit.")
        published_start = metadata.get("verified_contiguous_start")
        published_end = metadata.get("verified_contiguous_end") or metadata.get("target")
        if not published_start or not published_end:
            raise ValueError("No published uninterrupted SPY range to backtest.")
        first, last = map(date.fromisoformat, (published_start, published_end))
        start, end = start or first, end or last
        if not first <= start <= end <= last:
            raise ValueError(f"Select a replay range within published SPY coverage {first} through {last}.")
        calendar = sessions(first, last)
        signal_days = [session for session in calendar if start <= session.date <= end]
        if not signal_days:
            raise ValueError("No NYSE signal sessions in the selected date range.")
        days = [session for session in calendar if session.date <= signal_days[-1].date]
        if "next_session_open" in modes:
            signal_end = signal_days[-1].date
            candidate = sessions(signal_end + timedelta(days=1), signal_end + timedelta(days=14))[0]
            try:
                tail_bars = _verified_bars(db, candidate)
            except ValueError as exc:
                tail_error = str(exc)
            else:
                tail = candidate
                days.append(tail)
        bars = _verified_bars(db, days[0])
        for i, session in enumerate(days):
            following = days[i + 1] if i + 1 < len(days) else None
            next_bars = (tail_bars if following == tail else _verified_bars(db, following)) if following else None
            for bar in bars:
                digest.update(BAR_FINGERPRINT.pack(bar.time, bar.open, bar.high, bar.low, bar.close, bar.volume,
                                                   bar.wap if bar.wap is not None else 0, bar.wap is not None))
            atrs = [atr.update(bar) for bar in bars]
            if session.date < start:
                warmup_bars += len(bars)
                bars = next_bars
                continue
            day = session.date.isoformat()
            upper, lower, level_status = _levels(daily.get(day), bars[0].open)
            months.setdefault(day[:7], []).append({
                "date": day, "open_utc": session.open_utc, "close_utc": session.close_utc, "upper": upper, "lower": lower,
                "levels_status": level_status, "bars": _chart_bars(bars), "exit_only": session == tail,
            })
            if session != tail:
                signal_bars += len(bars)
                if benchmark_open is None:
                    benchmark_open = bars[0].open
                    _point(benchmark, session.open_utc, initial)
                _point(benchmark, session.close_utc, initial * bars[-1].close / benchmark_open)
                next_open = (following.open_utc, next_bars[0].open) if following else None
                for case in cases:
                    mode = case["id"]
                    equity = balances[mode]
                    if not case["equity"]:
                        _point(case["equity"], session.open_utc, equity)
                    result = replay_day(session, bars, atrs, upper, equity, settings.entry, settings.account,
                                        mode, next_open=next_open)
                    reason = result.skip_reason
                    if reason == "daily_upper_unavailable":
                        reason = level_status
                    if reason:
                        case["skips"][reason] += 1
                    case["events"].extend({**asdict(event), "date": day} for event in result.events)
                    trade = result.trade
                    if trade is not None:
                        if trade.exit_time > session.close_utc:
                            _point(case["equity"], session.close_utc, equity)
                        equity += trade.pnl
                        exit_day = datetime.fromtimestamp(trade.exit_time, NEW_YORK).date().isoformat()
                        case["trades"].append({**asdict(trade), "date": day, "exit_date": exit_day,
                                                "equity_after": equity})
                        _point(case["equity"], trade.exit_time, equity)
                        balances[mode] = equity
                    if trade is None or trade.exit_time <= session.close_utc:
                        _point(case["equity"], session.close_utc, equity)
            bars = next_bars
    for case in cases:
        case["stats"] = _stats(case, initial)
    return {
        "ticker": settings.ticker, "settings": _wire(settings), "scenarios": cases, "months": months, "benchmark": benchmark,
        "fingerprints": {"intraday_sha256": digest.hexdigest(), "daily_sha256": daily_digest},
        "coverage": {"start": signal_days[0].date.isoformat(), "end": signal_days[-1].date.isoformat(),
                     "cache_start": published_start, "cache_end": published_end, "signal_sessions": len(signal_days),
                     "signal_bars": signal_bars, "warmup_bars": warmup_bars,
                     "next_open_tail": tail.date.isoformat() if tail else None, "volume_unit": metadata["volume_unit"],
                     **({"next_open_tail_unavailable": tail_error} if tail_error else {})},
    }


def output_path(path: Path | None, settings: BacktestSettings, coverage: dict) -> Path:
    candidate = (path or Path("output") / f"SPY_5m_entry1_{coverage['start']}_{coverage['end']}.html").resolve()
    protected = [settings.intraday_cache, settings.daily_cache]
    protected.extend(Path(str(settings.intraday_cache) + suffix) for suffix in ("-wal", "-shm", "-journal"))
    for source in protected:
        if candidate == source.resolve() or (candidate.exists() and source.exists() and candidate.samefile(source)):
            raise ValueError("Report output cannot overwrite an input cache or its sidecars.")
    if candidate.suffix.lower() != ".html":
        raise ValueError("Report output must be an .html file.")
    if candidate.exists():
        raise ValueError("Report already exists; use a different --output to keep the previous run.")
    return candidate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/SPY.toml"))
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--entry1-start-time", help="Override the first qualifying candle's New York start time.")
    parser.add_argument("--entry1-end-time", help="Override the last qualifying candle's New York start time.")
    args = parser.parse_args(argv)
    try:
        from .spy_5m_report import write_report

        settings = load_settings(args.config, entry1_start_time=args.entry1_start_time, entry1_end_time=args.entry1_end_time)
        data = run(settings, args.start, args.end)
        path = output_path(args.output, settings, data["coverage"])
        if path == args.config.resolve() or (args.config.exists() and path.exists() and path.samefile(args.config)):
            raise ValueError("Report output cannot overwrite the settings file.")
        write_report(path, data)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"SPY backtest: {exc}", file=sys.stderr)
        return 1
    for case in data["scenarios"]:
        stats = case["stats"]
        print(f"{case['label']}: {stats['trades']:,} trades; net P&L ${stats['net_pnl']:,.2f}.")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
