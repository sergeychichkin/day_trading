"""Replay SPY five-minute Entry 1 against verified local history."""

import argparse
import hashlib
import json
import math
import os
import sqlite3
import struct
import sys
import tomllib
from collections import Counter, deque
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from .opening_range import WilderATR, commission
from .sessions import NEW_YORK
from .spy_5m_replay import AccountSettings, EntrySettings, replay_day
from .spy_5m_report import _validate_windows_output_name
from .spy_5m_snapshot import SQLITE_SIDECARS, read_snapshot
from .spy_5m_store import BAR_FIELDS, FiveMinuteBar, expected_times, normalize_bar, publication, sessions
from .spy_daily import DAILY_IDENTITY

SPY_CONTRACT = "756733"
BAR_FINGERPRINT = struct.Struct("!q6d?")
LEGACY_SPY_DAILY_SHA256 = "b955d740c3111250be921f84ecbffd72c37fe2fda8c8c255496a9a27918e9577"
LABELS = {"same_day_close": "Same-day close", "next_session_open": "Next-session open",
          "trailing_stop": "Exit 3 · Trailing stop"}


@dataclass(frozen=True)
class ChartSettings:
    high_atr_multiplier: float = 0.25
    low_atr_multiplier: float = 0.5


@dataclass(frozen=True)
class BacktestSettings:
    ticker: str
    intraday_cache: Path
    daily_cache: Path
    entry: EntrySettings
    account: AccountSettings
    same_day_close: bool
    next_session_open: bool
    trailing_stop: bool = False
    chart: ChartSettings = ChartSettings()


def _table(config, name, allowed):
    section = config.get(name, {})
    if not isinstance(section, dict) or set(section) - set(allowed):
        raise ValueError(f"Unexpected settings in [{name}].")
    return section.copy()


def _numeric(value, key):
    if type(value) not in (int, float, Decimal):
        raise ValueError(f"{key} must be a finite number.")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{key} must be a finite number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{key} must be a finite number.")
    return number


def _settings_entry(values):
    for key in ("entry1_start_time", "entry1_end_time"):
        if key in values and not isinstance(values[key], time):
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


def load_settings(path: Path) -> BacktestSettings:
    with path.open("rb") as file:
        data = tomllib.load(file, parse_float=Decimal)
    if set(data) - {"ticker", "intraday_cache", "daily_cache", "entry1", "account", "exits", "chart"}:
        raise ValueError("SPY settings contain an unknown top-level key.")
    if data.get("ticker") != "SPY":
        raise ValueError("The five-minute runner currently supports only a verified SPY cache.")
    inputs = []
    for key in ("intraday_cache", "daily_cache"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"Set {key} to an input path.")
        inputs.append((path.parent / data[key]).resolve())
    values = _table(data, "entry1", [field.name for field in fields(EntrySettings)])
    entry = _settings_entry(values)
    account_values = _table(data, "account", [f.name for f in fields(AccountSettings)])
    for key, value in account_values.items():
        finite = _numeric(value, key)
        account_values[key] = finite if key == "tick_size" else Decimal(str(value))
    account = AccountSettings(**account_values)
    if float(account.starting_balance) <= 0 or account.tick_size <= 0 or not 0 < account.risk_pct <= 100:
        raise ValueError("starting_balance, tick_size, and risk_pct must be positive; risk_pct cannot exceed 100.")
    if not 0 < account.cash_cap <= 1:
        raise ValueError("cash_cap must be greater than zero and at most 1.")
    if not 0 <= account.slippage_bps < 10_000 or account.slippage_floor < 0:
        raise ValueError("Slippage must be nonnegative and slippage_bps below 10,000.")
    chart = _table(data, "chart", [field.name for field in fields(ChartSettings)])
    for key, value in chart.items():
        chart[key] = _numeric(value, key)
        if chart[key] < 0:
            raise ValueError(f"{key} cannot be negative.")
    exits = dict(same_day_close=True, next_session_open=False, trailing_stop=False)
    exits.update(_table(data, "exits", exits))
    if any(type(value) is not bool for value in exits.values()) or not any(exits.values()):
        raise ValueError("Enable at least one exit with boolean settings.")
    return BacktestSettings("SPY", *inputs, entry, account, chart=ChartSettings(**chart), **exits)


def _positive(value):
    try:
        number = _numeric(value, "price")
    except ValueError:
        return None
    return number if number > 0 else None


def _daily_snapshot(path):
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    snapshot = json.loads(raw)
    rows = snapshot.get("bars") if isinstance(snapshot, dict) else None
    if not isinstance(rows, list):
        raise ValueError("SPY daily snapshot has no bars list.")
    known_legacy = not DAILY_IDENTITY.keys() & snapshot.keys() and digest == LEGACY_SPY_DAILY_SHA256
    if not known_legacy and any(snapshot.get(key) != value for key, value in DAILY_IDENTITY.items()):
        raise ValueError("Invalid Yahoo Finance SPY daily snapshot identity; refresh it with qqq_viewer.spy_daily.")
    daily = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(day := row.get("time"), str):
            raise ValueError("SPY daily snapshot has an invalid date row.")
        if day in daily:
            raise ValueError(f"Duplicate SPY daily snapshot date: {day}.")
        daily[day] = row
    return daily, digest


def _opening_matches(daily_open, opening):
    return abs(Decimal(str(opening)) - Decimal(str(daily_open))) <= Decimal(str(daily_open)) * Decimal("0.05")


def _lower_level(row, daily_open):
    lower = _positive(row.get("lower"))
    return lower if lower is not None and 0.5 <= lower / daily_open <= 1 else None


def _levels(row, opening):
    if row is None:
        return None, None, "daily_upper_unavailable"
    daily_open = _positive(row.get("open"))
    if daily_open is None:
        return None, None, "daily_open_unavailable"
    if not _opening_matches(daily_open, opening):
        return None, None, "opening_price_mismatch"
    upper, lower = _positive(row.get("upper")), _lower_level(row, daily_open)
    if upper is None or not 1 <= upper / daily_open <= 2:
        return None, lower, "daily_upper_unavailable"
    return upper, lower, "available" if lower is not None else "daily_lower_unavailable"


def _prior_lower(day, row, opening):
    daily_open = _positive(row.get("open"))
    lower = _lower_level(row, daily_open) if daily_open is not None else None
    if lower is None:
        return {"date": day, "lower": None, "status": "daily_lower_unavailable"}
    if opening is None:
        status = "daily_only_before_intraday_coverage"
    elif _opening_matches(daily_open, opening):
        status = "verified"
    else:
        lower, status = None, "opening_price_mismatch"
    return {"date": day, "lower": lower, "status": status}


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


def _fingerprint_bars(digest, bars):
    for bar in bars:
        digest.update(BAR_FINGERPRINT.pack(bar.time, bar.open, bar.high, bar.low, bar.close, bar.volume,
                                           bar.wap if bar.wap is not None else 0, bar.wap is not None))


def _point(points, timestamp, balance):
    record = {"time": timestamp, "value": float(balance)}
    if points and points[-1]["time"] == timestamp:
        points[-1] = record
    else:
        points.append(record)


def _stats(case, starting):
    trades = case["trades"]
    gains = math.fsum(trade["pnl"] for trade in trades if trade["pnl"] > 0)
    losses = math.fsum(-trade["pnl"] for trade in trades if trade["pnl"] < 0)
    profit_factor = gains / losses if losses else ("infinite" if gains else None)
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
        "profit_factor": profit_factor,
        "ambiguous": sum(trade["ambiguous"] for trade in trades),
        **{key: sum(trade[key] for trade in trades) for key in ("commission", "slippage", "gap_slippage")},
    }


def _chart_bars(bars, atrs, volume_averages):
    weighted = volume = 0.0
    rows = []
    for bar, atr, average in zip(bars, atrs, volume_averages, strict=True):
        if bar.volume:
            weighted += bar.wap * bar.volume
            volume += bar.volume
        rows.append({**asdict(bar), "vwap": weighted / volume if volume else None,
                     "atr": atr, "volume_sma_20": average})
    return rows


def _wire(settings):
    data = asdict(settings)
    exact = {}
    for key, value in data["account"].items():
        if isinstance(value, Decimal):
            data["account"][key] = float(value)
            if Decimal(str(data["account"][key])) != value:
                exact[key] = str(value)
    if exact:
        data["account_exact"] = exact
    for key in ("intraday_cache", "daily_cache"):
        data[key] = str(data[key])
    for key in ("entry1_start_time", "entry1_end_time"):
        data["entry"][key] = data["entry"][key].isoformat(timespec="minutes")
    return data


def _ny_date(timestamp):
    return datetime.fromtimestamp(timestamp, NEW_YORK).date().isoformat()


def _position_mark(position, session, close):
    entry_fee = Decimal(str(commission(position.shares, position.entry_price)))
    pnl = (Decimal(str(close)) - position.entry_price) * position.shares - entry_fee
    return {
        "entry_time": position.entry_time, "entry_date": _ny_date(position.entry_time),
        "signal_time": position.signal_time, "bear_time": position.bear_time,
        "entry_price": float(position.entry_price), "initial_stop": position.initial_stop,
        "active_stop": position.active_stop, "pending_stop": position.pending_stop,
        "shares": position.shares, "stop_adjustments": [asdict(item) for item in position.stop_adjustments],
        "mark_price": close, "mark_time": session.close_utc, "unrealized_pnl": float(pnl),
    }


def run(settings: BacktestSettings, start: date | None = None, end: date | None = None) -> dict:
    if settings.ticker != "SPY":
        raise ValueError("The five-minute runner supports only SPY.")
    daily, daily_digest = _daily_snapshot(settings.daily_cache)
    modes = [key for key in LABELS if getattr(settings, key)]
    if not modes:
        raise ValueError("Enable at least one exit.")
    cases = [{"id": mode, "label": LABELS[mode], "trades": [], "events": [], "equity": [],
              "skips": Counter(), "open_position": None}
             for mode in modes]
    starting_cash = Decimal(str(settings.account.starting_balance))
    initial = float(starting_cash)
    balances = dict.fromkeys(modes, starting_cash)
    months, benchmark = {}, []
    digest = hashlib.sha256()
    atr = WilderATR(20)
    volumes = deque(maxlen=20)
    prior_levels = deque(maxlen=2)
    position = None
    benchmark_open = None
    warmup_bars = signal_bars = 0
    tail = None
    tail_bars = None
    tail_error = None
    with read_snapshot(settings.intraday_cache) as db:
        metadata = dict(db.execute("SELECT key,value FROM metadata"))
        if metadata.get("contract") != SPY_CONTRACT or metadata.get("volume_unit") not in {"shares", "lots"}:
            raise ValueError("The intraday cache does not identify the expected SPY contract and volume unit.")
        bounds = publication(db)
        if bounds is None:
            raise ValueError("No published uninterrupted SPY range to backtest.")
        published_start, published_end = bounds
        first, last = map(date.fromisoformat, (published_start, published_end))
        start, end = start or first, end or last
        if not first <= start <= end <= last:
            raise ValueError(f"Select a replay range within published SPY coverage {first} through {last}.")
        calendar = sessions(first, last)
        if not calendar or calendar[0].date != first or calendar[-1].date != last:
            raise ValueError("Published SPY endpoints must both be NYSE trading sessions.")
        signal_days = [session for session in calendar if start <= session.date <= end]
        if not signal_days:
            raise ValueError("No NYSE signal sessions in the selected date range.")
        days = [session for session in calendar if session.date <= signal_days[-1].date]
        signal_end = signal_days[-1].date
        candidate = None
        if settings.trailing_stop:
            before_cache = sessions(first - timedelta(days=14), first - timedelta(days=1))
            for session in before_cache[-2:]:
                day = session.date.isoformat()
                row = daily.get(day, {})
                level = _prior_lower(day, row, None)
                if level["lower"] is not None:
                    try:
                        prior_bars = _verified_bars(db, session)
                    except ValueError:
                        pass  # No verified five-minute open for this pre-publication session.
                    else:
                        _fingerprint_bars(digest, prior_bars)
                        level = _prior_lower(day, row, prior_bars[0].open)
                prior_levels.appendleft(level)
        if settings.trailing_stop or settings.next_session_open:
            candidate = next(iter(sessions(signal_end + timedelta(days=1), signal_end + timedelta(days=14))), None)
        if settings.next_session_open:
            if candidate is None:
                tail_error = "No next NYSE session found in the exchange calendar lookup."
            else:
                try:
                    tail_bars = _verified_bars(db, candidate)
                except ValueError as exc:
                    day = candidate.date.isoformat()
                    if candidate.date > last and not any(
                        db.execute(f"SELECT 1 FROM {table} WHERE date=? LIMIT 1", (day,)).fetchone()
                        for table in ("bars", "sessions", "attempts", "conflicts")
                    ):
                        tail_error = f"No verified session in cache after {signal_end}."
                    else:
                        tail_error = str(exc)
                else:
                    tail = candidate
                    days.append(tail)
        bars = _verified_bars(db, days[0])
        for i, session in enumerate(days):
            following = days[i + 1] if i + 1 < len(days) else None
            next_bars = (tail_bars if following == tail else _verified_bars(db, following)) if following else None
            _fingerprint_bars(digest, bars)
            atrs = [atr.update(bar) for bar in bars]
            volume_averages = []
            for bar in bars:
                volumes.append(bar.volume)
                volume_averages.append(math.fsum(volumes) / 20 if len(volumes) == 20 else None)
            day = session.date.isoformat()
            priors = list(prior_levels)
            if settings.trailing_stop and session != tail:
                prior_levels.appendleft(_prior_lower(day, daily.get(day, {}), bars[0].open))
            if session.date < start:
                warmup_bars += len(bars)
                bars = next_bars
                continue
            upper, lower, level_status = _levels(daily.get(day), bars[0].open)
            chart_session = {
                "date": day, "open_utc": session.open_utc, "close_utc": session.close_utc, "upper": upper, "lower": lower,
                "levels_status": level_status, "bars": _chart_bars(bars, atrs, volume_averages),
                "exit_only": session == tail,
            }
            if settings.trailing_stop and session != tail:
                chart_session["prior_lowers"] = priors
            months.setdefault(day[:7], []).append(chart_session)
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
                    trailing = mode == "trailing_stop"
                    upcoming = following or candidate
                    result = replay_day(
                        session, bars, atrs, upper, equity, settings.entry, settings.account, mode,
                        next_open=next_open, position=position if trailing else None,
                        prior_lowers=tuple(item["lower"] for item in priors) if trailing else (None, None),
                        next_bar_open=upcoming.open_utc if trailing and upcoming else None,
                    )
                    if trailing:
                        position = result.position
                        if position is not None and session.date == signal_end:
                            case["open_position"] = _position_mark(position, session, bars[-1].close)
                    reason = result.skip_reason
                    if reason == "daily_upper_unavailable":
                        reason = level_status
                    if reason:
                        case["skips"][reason] += 1
                    case["events"].extend({**asdict(event), "date": _ny_date(event.time)} for event in result.events)
                    for trade in result.completed_trades:
                        if trade.exit_time > session.close_utc:
                            _point(case["equity"], session.close_utc, equity)
                        equity += trade.pnl
                        case["trades"].append({**asdict(trade), "date": _ny_date(trade.entry_time),
                                                "exit_date": _ny_date(trade.exit_time),
                                                "pnl": float(trade.pnl), "equity_after": float(equity)})
                        _point(case["equity"], trade.exit_time, equity)
                        balances[mode] = equity
                    if not result.completed_trades or result.completed_trades[-1].exit_time <= session.close_utc:
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


def output_path(path: Path | None, settings: BacktestSettings, coverage: dict | None = None, *,
                config_path: Path | None = None) -> Path:
    if path is None:
        if coverage is None:
            raise ValueError("Coverage is needed to name the default report.")
        path = Path("output") / f"SPY_5m_entry1_{coverage['start']}_{coverage['end']}.html"
    if os.name == "nt":
        _validate_windows_output_name(path)
    candidate = path.resolve()
    if os.name == "nt":
        _validate_windows_output_name(candidate)
    protected = [settings.intraday_cache, settings.daily_cache]
    protected.extend(Path(str(settings.intraday_cache) + suffix) for suffix in SQLITE_SIDECARS)
    for source in protected:
        if candidate == source.resolve() or (candidate.exists() and source.exists() and candidate.samefile(source)):
            raise ValueError("Report output cannot overwrite an input cache or its sidecars.")
    if config_path is not None and (
        candidate == config_path.resolve()
        or (candidate.exists() and config_path.exists() and candidate.samefile(config_path))
    ):
        raise ValueError("Report output cannot overwrite the settings file.")
    if candidate.suffix.lower() != ".html":
        raise ValueError("Report output must be an .html file.")
    if candidate.exists():
        raise ValueError("Report already exists; use a different --output to keep the previous run.")
    for parent in candidate.parents:
        if parent.exists():
            if not parent.is_dir():
                raise ValueError(f"Report output parent is not a directory: {parent}")
            break
    return candidate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/SPY.toml"))
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        from .spy_5m_report import write_report

        settings = load_settings(args.config)
        path = output_path(args.output, settings, config_path=args.config) if args.output is not None else None
        data = run(settings, args.start, args.end)
        if path is None:
            path = output_path(None, settings, data["coverage"], config_path=args.config)
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
