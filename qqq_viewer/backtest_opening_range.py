import argparse
import hashlib
import math
import re
import sqlite3
import struct
import tomllib
from dataclasses import asdict, replace
from datetime import date, datetime
from pathlib import Path

from .opening_range import EntrySettings, Settings, WilderATR, replay_session, session_vwap
from .opening_range_report import write_report
from .sessions import NEW_YORK, Session, sessions
from .store import BAR_COLUMNS, Bar, Store

DEFAULT_START = date(2020, 1, 1)
DEFAULT_END = date(2020, 12, 31)
PORTABLE_BAR = struct.Struct("<q5d")
LEGACY_BASELINE = Path("output/QQQ_opening_range_2020-01-01_2020-12-31.html")


def _validate_numbers(values: dict, integer: tuple[str, ...]) -> None:
    for name, value in values.items():
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be a finite, nonnegative number.")
        if name in integer and type(value) is not int:
            raise ValueError(f"{name} must be an integer.")


def load_settings(path: Path) -> tuple[str, Path, Settings]:
    data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    ticker = data["ticker"]
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", ticker):
        raise ValueError("Ticker must be an uppercase US stock or ETF symbol.")
    strategy = dict(data.get("strategy", {}))
    legacy = {name: strategy.pop(name) for name in asdict(EntrySettings()) if name != "enabled" and name in strategy}
    if legacy and ("entry1" in data or "entry2" in data):
        raise ValueError("Use either flat entry settings in [strategy] or [entry1] and [entry2] tables.")
    entry1 = EntrySettings(**data.get("entry1", legacy))
    entry2 = EntrySettings(**({"enabled": False} | data.get("entry2", {})))
    settings = Settings(entry1=entry1, entry2=entry2, **strategy)
    _validate_numbers({k: v for k, v in asdict(settings).items() if k not in ("entry1", "entry2")},
                      ("trail_lookback", "daily_atr_period"))
    if min(settings.starting_balance, settings.leverage, settings.tick_size, settings.daily_atr_period) <= 0:
        raise ValueError("Account size, leverage, tick size and daily ATR period must be positive.")
    for entry in (entry1, entry2):
        values = asdict(entry)
        if type(values.pop("enabled")) is not bool:
            raise ValueError("An enabled flag must be true or false.")
        _validate_numbers(values, ("entry_lookback", "stop_lookback"))
        if not 0 < entry.risk_pct <= 100:
            raise ValueError("Entry risk_pct must be greater than zero and at most 100.")
    if not (entry1.enabled or entry2.enabled):
        raise ValueError("At least one entry must be enabled.")
    return ticker, (path.parent / data["cache"]).resolve(), settings


def _same_path(first: Path, second: Path) -> bool:
    return first.resolve() == second.resolve() or (
        first.is_file() and second.is_file() and first.samefile(second)
    )


def output_path(ticker: str, start: date, end: date, explicit: Path | None,
                *, cache: Path | None = None) -> Path:
    path = explicit or Path("output") / f"{ticker}_opening_range_rule_comparison_{start}_{end}.html"
    checkout = Path(__file__).resolve().parents[1]
    previous_reports = {
        LEGACY_BASELINE: "The original 2020 report is reserved as the baseline.",
        Path("output/QQQ_opening_range_entries_2020-01-01_2020-12-31.html"):
            "The previous comparison report is reserved.",
        Path("output") / f"{ticker}_opening_range_entries_{start}_{end}.html":
            "The previous comparison report is reserved.",
    }
    for previous, message in previous_reports.items():
        if _same_path(path, previous) or _same_path(path, checkout / previous):
            raise ValueError(f"{message} Choose another output path.")
    if cache is not None:
        protected = {base.with_name(base.name + suffix) for base in (cache, cache.resolve())
                     for suffix in ("", "-wal", "-shm", "-journal")}
        if any(_same_path(path, candidate) for candidate in protected):
            raise ValueError("Report output must be separate from the input cache and SQLite sidecars.")
    return path


def _scenarios(cfg: Settings) -> list[tuple[dict, Settings]]:
    e1, e2 = cfg.entry1, cfg.entry2
    modes = []
    if e1.enabled:
        modes.append(("entry1", "Entry 1 only", ["entry1"], replace(cfg, entry2=replace(e2, enabled=False))))
    if e2.enabled:
        modes.append(("entry2", "Entry 2 only", ["entry2"], replace(cfg, entry1=replace(e1, enabled=False))))
    if e1.enabled and e2.enabled:
        modes.append(("both", "Both entries", ["entry1", "entry2"], cfg))
    if not modes:
        raise ValueError("At least one entry must be enabled.")
    return [
        ({"id": f"{version}-{key}-{suffix}", "scenario_id": f"{key}-{suffix}", "rule_version": version,
          "label": label, "entries": entries, "slippage_multiplier": multiplier,
          "trades": [], "events": [], "equity": []}, settings)
        for version in ("legacy", "revised")
        for suffix, multiplier in (("base", 1.0), ("stress", 2.0))
        for key, label, entries, settings in modes
    ]


def stats(trades: list[dict], balances: list[float], initial: float) -> dict:
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    peak = initial
    max_drawdown = 0.0
    max_drawdown_dollars = 0.0
    for balance in balances:
        peak = max(peak, balance)
        max_drawdown = max(max_drawdown, (peak - balance) / peak * 100)
        max_drawdown_dollars = max(max_drawdown_dollars, peak - balance)
    return {
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "ending_balance": balances[-1], "net": balances[-1] - initial,
        "return_pct": (balances[-1] / initial - 1) * 100,
        "max_drawdown_pct": max_drawdown, "max_drawdown_usd": max_drawdown_dollars,
        "avg_trade": sum(pnls) / len(pnls) if pnls else None,
        "profit_factor": sum(wins) / -sum(losses) if losses else None,
        "commission": sum(t["commission"] for t in trades),
        "slippage": sum(t["slippage"] for t in trades),
        "gap_slippage": sum(t["gap_slippage"] for t in trades),
        "ambiguous": sum(t["ambiguous"] for t in trades),
    }


def _append_balance(points: list[dict], timestamp: int, value: float) -> None:
    point = {"time": timestamp, "value": value}
    if points and points[-1]["time"] == timestamp:
        points[-1] = point
    else:
        points.append(point)


def _verified_bars(db: sqlite3.Connection, session: Session, ticker: str) -> list[Bar]:
    day = session.date.isoformat()
    bounds = day, session.open_utc, session.close_utc, ticker
    bar_columns = ", ".join("b." + name for name in BAR_COLUMNS.split(", "))
    sources = db.execute(
        f"""SELECT {bar_columns}, a.session_date, a.open_utc, a.close_utc,
                   json_extract(a.request, '$.symbol')
            FROM bars b LEFT JOIN attempts a ON a.id=b.first_attempt
            WHERE b.time>=? AND b.time<? ORDER BY b.time""",
        (session.open_utc, session.close_utc),
    ).fetchall()
    if [row[0] for row in sources] != list(session.minutes):
        raise ValueError(f"Incomplete regular-session input on {day}.")
    if any(row[6:] != bounds for row in sources):
        raise ValueError(f"Unverified canonical bar source for {ticker} on {day}.")
    completion = db.execute(
        """SELECT 1 FROM attempts WHERE session_date=? AND open_utc=? AND close_utc=?
           AND json_extract(request, '$.symbol')=? AND status='complete' LIMIT 1""",
        bounds,
    ).fetchone()
    if completion is None:
        raise ValueError(f"Missing verified session completion for {ticker} on {day}.")
    return [Bar(*row[:6]) for row in sources]


def run(ticker: str, store: Store, settings: Settings, start: date, end: date) -> dict:
    if start > end:
        raise ValueError("The start date must be on or before the end date.")
    cases = _scenarios(settings)
    fast_atr, daily_atr = WilderATR(20), WilderATR(settings.daily_atr_period)
    months = {}
    benchmark = []
    benchmark_open = None
    ratio = None
    digest = hashlib.sha256()
    input_count = tested_count = 0
    with store.reader() as db:
        symbols = {row[0] for row in db.execute(
            "SELECT DISTINCT json_extract(request, '$.symbol') FROM attempts "
            "WHERE json_extract(request, '$.symbol') IS NOT NULL"
        )}
        if symbols != {ticker}:
            raise ValueError(f"{ticker} configuration selects a cache identifying {sorted(symbols)}.")
        first_time = db.execute("SELECT MIN(time) FROM bars").fetchone()[0]
        if first_time is None:
            raise ValueError("No minute bars in the cache.")
        cached_start = datetime.fromtimestamp(first_time, NEW_YORK).date()
        calendar = sessions(min(start, cached_start), end)
        if not any(s.date >= start for s in calendar):
            raise ValueError("No US regular sessions in the requested period.")
        for session in calendar:
            bars = _verified_bars(db, session, ticker)
            if db.execute(
                "SELECT 1 FROM conflicts WHERE time>=? AND time<? LIMIT 1",
                (session.open_utc, session.close_utc),
            ).fetchone():
                raise ValueError(f"Conflicting input bars on {session.date.isoformat()}.")
            for bar in bars:
                digest.update(PORTABLE_BAR.pack(bar.time, bar.open, bar.high, bar.low, bar.close, bar.volume))
            input_count += len(bars)
            highs = [b.high for b in bars]
            lows = [b.low for b in bars]
            atrs = [fast_atr.update(bar) for bar in bars]
            if session.date >= start:
                tested_count += len(bars)
                day = session.date.isoformat()
                vwaps = session_vwap(bars)
                for case, cfg in cases:
                    points = case["equity"]
                    if not points:
                        _append_balance(points, session.open_utc, cfg.starting_balance)
                    balance = points[-1]["value"]
                    result = replay_session(session, bars, atrs, balance, cfg, ratio, case["slippage_multiplier"],
                                            vwaps=vwaps, rule_version=case["rule_version"])
                    for trade in result.trades:
                        balance += trade.pnl
                        _append_balance(points, trade.exit_time, balance)
                    _append_balance(points, session.close_utc, result.ending_balance)
                    case["trades"].extend(asdict(trade) | {"date": day} for trade in result.trades)
                    case["events"].extend(asdict(event) | {"date": day} for event in result.events)
                if benchmark_open is None:
                    benchmark_open = bars[0].open
                    _append_balance(benchmark, session.open_utc, settings.starting_balance)
                _append_balance(benchmark, session.close_utc, settings.starting_balance * bars[-1].close / benchmark_open)
                months.setdefault(day[:7], []).append({
                    "date": day, "close": session.close_utc,
                    "range_high": max(highs[:15]), "range_low": min(lows[:15]),
                    "bars": [[b.time, b.open, b.high, b.low, b.close, b.volume,
                              round(v, 5) if v is not None else None]
                             for b, v in zip(bars, vwaps)],
                })
            daily = Bar(session.open_utc, bars[0].open, max(highs), min(lows), bars[-1].close, 0.0)
            value = daily_atr.update(daily)
            ratio = 100 * value / daily.close if value is not None else None
    initial = settings.starting_balance
    for case, _ in cases:
        trades = case["trades"]
        case["stats"] = stats(trades, [point["value"] for point in case["equity"]], initial)
        case["trades_without_daily_atr"] = sum(t["atr_close_pct"] is None for t in trades)
    return {
        "ticker": ticker, "start": start.isoformat(), "end": end.isoformat(),
        "settings": asdict(settings), "months": months, "benchmark": benchmark,
        "benchmark_pct": (benchmark[-1]["value"] / initial - 1) * 100,
        "scenarios": [case for case, _ in cases], "bars": tested_count, "input_bars": input_count,
        "input_sha256": digest.hexdigest(), "sessions": len(benchmark) - 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare intraday opening-range entries.")
    parser.add_argument("--config", type=Path, default=Path("config/QQQ.toml"))
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--end", type=date.fromisoformat, default=DEFAULT_END)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        ticker, cache, cfg = load_settings(args.config)
        output = output_path(ticker, args.start, args.end, args.output, cache=cache)
        if not cache.is_file():
            raise ValueError(f"Cache file unavailable: {cache}")
        report = run(ticker, Store(cache, read_only=True), cfg, args.start, args.end)
        write_report(output, report)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.error(str(error))
    for case in report["scenarios"]:
        result = case["stats"]
        print(f"{case['id']}: {result['trades']} trades, net PnL ${result['net']:,.2f}, "
              f"return {result['return_pct']:.2f}%, drawdown {result['max_drawdown_pct']:.2f}%")
    print(f"Executed on {report['bars']:,} verified bars in {report['sessions']} sessions. Report: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
