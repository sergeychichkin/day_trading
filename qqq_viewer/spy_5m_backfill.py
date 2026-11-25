"""Download completed SPY five-minute regular-hours trade bars from TWS."""

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from itertools import groupby
from pathlib import Path
from time import monotonic

from ib_async import Stock, util
from ib_async.wrapper import RequestError

from .sessions import NEW_YORK, Session
from .spy_5m_store import DEFAULT_DB, FiveMinuteBar, FiveMinuteStore, completed, expected_times, normalize_bar
from .spy_daily import DEFAULT_CACHE
from .tws import ContractIdentity, TwsHistory, classify_error

STOP = {"disconnected", "entitlement", "pacing"}
LISTINGS = {"ARCA", "NYSE ARCA", "NYSEARCA"}


class SpyHistory(TwsHistory):
    def __init__(self, port: int, client_id: int, *, min_interval: float = 2.0):
        super().__init__(port, client_id, 25, min_interval=min_interval)
        self.identity = None

    def qualify_spy(self) -> ContractIdentity:
        try:
            details = util.run(self._ib.reqContractDetailsAsync(Stock("SPY", "SMART", "USD")),
                               timeout=self.timeout_seconds)
        except (RequestError, TimeoutError) as exc:
            raise ConnectionError(f"TWS could not qualify SPY: {exc}") from exc
        matches = {ContractIdentity(contract.conId, contract.primaryExchange)
                   for row in details if (contract := row.contract).symbol == "SPY"
                   and contract.secType == "STK" and contract.currency == "USD"
                   and contract.conId > 0 and contract.primaryExchange.upper() in LISTINGS}
        if len(matches) != 1:
            raise ValueError(f"Expected one ARCA-listed USD SPY contract, TWS returned {len(matches)} matches.")
        self.identity = matches.pop()
        return self.identity

    def request(self, sessions: list[Session], duration: str) -> tuple[str, list[FiveMinuteBar], str]:
        if not self._ib.client.isReady():
            return "disconnected", [], "TWS API is disconnected."
        if self.identity is None:
            raise ValueError("Qualify SPY before requesting bars.")
        delay = self._next_request_at - monotonic()
        if delay > 0:
            self._ib.sleep(delay)
        self._next_request_at = monotonic() + self.min_interval
        contract = Stock("SPY", "SMART", "USD", conId=self.identity.con_id,
                         primaryExchange=self.identity.primary_exchange)
        end = datetime.fromtimestamp(sessions[-1].close_utc, timezone.utc).strftime("%Y%m%d-%H:%M:%S")
        timeout = 25 if duration == "1 D" else 75
        try:
            bars = util.run(self._ib.reqHistoricalDataAsync(
                contract, endDateTime=end, durationStr=duration, barSizeSetting="5 mins",
                whatToShow="TRADES", useRTH=True, formatDate=2, keepUpToDate=False, timeout=timeout,
            ), timeout=timeout + 5)
        except TimeoutError:
            return "timeout", [], f"No complete SPY response within {timeout} seconds."
        except RequestError as exc:
            return classify_error(exc.code, exc.message), [], f"TWS {exc.code}: {exc.message}"
        except OSError as exc:
            return "disconnected", [], str(exc)
        result = [FiveMinuteBar(int(bar.date.timestamp()), bar.open, bar.high, bar.low, bar.close,
                                bar.volume, bar.average) for bar in bars
                  if isinstance(bar.date, datetime) and bar.date.utcoffset() is not None]
        return ("complete", result, "") if result else ("empty", [], "TWS returned no usable five-minute timestamps.")


def _by_session(window, bars):
    owner = {time: session for session in window for time in expected_times(session)}
    rows = {session: [] for session in window}
    for bar in bars:
        session = owner.get(bar.time)
        if session is not None and (clean := normalize_bar(bar)) is not None:
            rows[session].append(clean)
    return rows


def _probe_window(store, client, window, duration, label):
    pending = set(store.pending(window))
    sample = {}
    months = {}
    for session in window:
        if session not in pending:
            month = session.date.isoformat()[:7]
            if month not in months:
                months[month] = {row["date"]: row for row in store.read_month(month)["sessions"]}
            sample[session] = [FiveMinuteBar(**bar) for bar in months[month][session.date.isoformat()]["bars"]]
    status, diagnostic = "complete", ""
    if pending:
        status, received, diagnostic = client.request(window, duration)
        observed = _by_session(pending, received)
        sample.update(observed)
    else:
        observed = {}
    usable = {}
    if status == "complete":
        for session, rows in sample.items():
            grid = expected_times(session)
            by_time = {bar.time: bar for bar in rows if bar.time in grid}
            if len(by_time) == len(grid) and all(bar.volume == 0 or bar.wap is not None for bar in by_time.values()):
                if any(bar.volume > 0 for bar in by_time.values()):
                    usable[session] = [by_time[time] for time in grid]
    print(f"{label} probe: {len(usable)}/{len(window)} usable sessions; "
          f"{sum(len(rows) for rows in sample.values()):,} accepted bars ({status})"
          f"{': ' + diagnostic if diagnostic else ''}.", flush=True)
    return usable, observed, status, diagnostic


def _price_check(daily_cache, samples):
    try:
        bars = json.loads(daily_cache.read_bytes())["bars"]
        daily = {row["time"]: row for row in bars}
    except (OSError, ValueError, KeyError, TypeError):
        return "Yahoo comparison unavailable (daily snapshot absent or unreadable).", None
    overlaps = 0
    for session, rows in samples.items():
        row = daily.get(session.date.isoformat())
        if row is None:
            continue
        opening, close = row.get("open"), row.get("close")
        if not all(isinstance(value, (int, float)) and math.isfinite(value) and value > 0
                   for value in (opening, close)):
            continue
        overlaps += 1
        a, b = rows[0].open / opening, rows[-1].close / close
        # Ignore normal venue/auction differences; flag a common scale factor
        # more than 5% from parity when open and close agree within 1%.
        if min(abs(a - 1), abs(b - 1)) > 0.05 and abs(a / b - 1) <= 0.01:
            return "", f"TWS/Yahoo price-basis mismatch on {session.date}: open ×{a:.3f}, close ×{b:.3f}."
    if not overlaps:
        return "Yahoo comparison unavailable (no overlapping usable daily bars).", None
    return f"TWS and Yahoo prices comparable on {overlaps} overlapping probe sessions.", None


def run_backfill(store: FiveMinuteStore, client, now: datetime, *, volume_unit: str, daily_cache: Path,
                 probe_only: bool = False) -> dict:
    target = completed(now)
    if not target:
        raise ValueError("No completed SPY sessions in the requested range.")
    through = target[-1].date
    price_check = ""
    scope = target

    def summary(diagnostic=""):
        return {"through": through.isoformat(), **store.coverage(scope),
                "price_check": price_check, "diagnostic": diagnostic}

    identity = client.qualify_spy()
    print(f"SPY contract {identity.con_id}, {identity.primary_exchange}; configured volume unit: {volume_unit}.",
          flush=True)
    store.configure(identity.con_id, volume_unit)
    jan = [session for session in target if session.date.isoformat() <= "2000-01-07"]
    recent = [target[-1]]
    if probe_only:
        scope = sorted(set(jan + recent), key=lambda session: session.date)
    elif not store.pending(target):
        return summary()

    old_sample, old_rows, old_status, old_diag = _probe_window(store, client, jan, "1 W", "January 2000")
    if not old_sample:
        return summary(f"January 2000 probe: {old_status}; {old_diag or 'no complete usable sessions'}")
    if recent[0] in jan and recent[0] in old_sample:
        new_sample, new_rows, new_status, new_diag = {recent[0]: old_sample[recent[0]]}, {}, "complete", ""
    else:
        new_sample, new_rows, new_status, new_diag = _probe_window(store, client, recent, "1 D", "Recent")
    if not new_sample:
        return summary(f"Recent probe: {new_status}; {new_diag or 'no complete usable sessions'}")
    price_check, mismatch = _price_check(daily_cache, old_sample | new_sample)
    if mismatch:
        return summary(mismatch)
    print(price_check, flush=True)

    store.set_target(through)
    for rows, status, diagnostic in ((old_rows, old_status, old_diag), (new_rows, new_status, new_diag)):
        for session, bars in rows.items():
            store.record(session, status, bars, diagnostic)
    if probe_only:
        return summary()

    pending = store.pending(target)
    for _, group in groupby(pending, key=lambda session: session.date.isocalendar()[:2]):
        window = list(group)
        status, bars, diagnostic = client.request(window, "1 W" if len(window) > 1 else "1 D")
        for session, rows in _by_session(window, bars).items():
            store.record(session, status, rows, diagnostic)
        print(f"{window[0].date} to {window[-1].date}: {status}, {len(bars):,} provider bars"
              f"{' — ' + diagnostic if diagnostic else ''}", flush=True)
        if status in STOP:
            return summary(f"{window[0].date}: {status}; {diagnostic}")
        if status == "broker_error":
            return summary(f"{window[0].date}: {status}; {diagnostic}")
        for session in store.pending(window):
            status, bars, diagnostic = client.request([session], "1 D")
            store.record(session, status, bars, diagnostic)
            if status in STOP or status == "broker_error":
                return summary(f"{session.date}: {status}; {diagnostic}")
    return summary()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7497)
    parser.add_argument("--client-id", type=int, default=15908)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--daily-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--volume-unit", choices=("shares", "lots"), required=True)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    try:
        with SpyHistory(args.port, args.client_id) as client:
            result = run_backfill(FiveMinuteStore(args.db), client, datetime.now(timezone.utc),
                                  volume_unit=args.volume_unit, daily_cache=args.daily_cache,
                                  probe_only=args.probe)
    except (ConnectionError, OSError, ValueError) as exc:
        print(f"SPY history: {exc}", file=sys.stderr)
        return 1

    def stamp(time):
        return datetime.fromtimestamp(time, NEW_YORK).isoformat(timespec="minutes") if time is not None else "none"

    print(f"{'Probe' if args.probe else 'Coverage'} through {result['through']}: "
          f"{result['received']:,}/{result['expected']:,} five-minute bars; "
          f"{result['missing']:,} missing, {result['unverified_sessions']:,} unverified sessions, "
          f"{result['conflict_count']:,} conflicting bars.")
    print(f"New York range: {stamp(result['first_bar'])} to {stamp(result['last_bar'])}. Cache: {args.db}")
    if result["diagnostic"]:
        print(result["diagnostic"], file=sys.stderr)
        return 1
    return int(not args.probe and bool(result["missing"] or result["unverified_sessions"] or result["conflict_count"]))


if __name__ == "__main__":
    raise SystemExit(main())
