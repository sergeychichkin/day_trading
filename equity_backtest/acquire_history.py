"""Download and audit ETF history in a resumable, run-specific directory."""

import argparse
import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path
from tempfile import mkdtemp
from threading import Lock
from time import monotonic, sleep

from filelock import FileLock

from .acquisition_archive import Archive, atomic_json, sha256_file
from .daily_history import daily_snapshot, download_daily
from .history_5m import sessions
from .history_audit import SessionEvidence, add_response, audit_symbol, publication_bundle, setup_writers
from .market_time import NEW_YORK
from .tws_history import EquityHistory

TICKERS = tuple("QQQ DIA XLF XLK XLE XLI XLP XLY XLV XLU XLB IYR IWM TLT TIP EMB PFF IBB EEM GLD SLV GDX USO".split())
FLOOR = date(2007, 1, 1)
INCEPTION = {"PFF": (date(2007, 3, 26), "https://www.ishares.com/us/products/239826/ishares-u-s-preferred-stock-etf"),
             "EMB": (date(2007, 12, 17), "https://www.ishares.com/us/products/239572/ishares-msci-emerging-markets-etf")}
PROTECTED = tuple(map(Path, ("data/qqq.sqlite3", "data/qqq.sqlite3-wal", "data/spy_5m.sqlite3", "data/spy_daily.json")))
RECOVER_CONNECTION = {"disconnected", "pacing"}


def cutoff_at(now):
    if now.utcoffset() is None:
        raise ValueError("Use a timezone-aware cutoff clock.")
    day = now.astimezone(NEW_YORK).date()
    return next(s.date for s in reversed(sessions(day - timedelta(days=14), day)) if s.close_utc <= now.timestamp())


def run_arguments(args, directory):
    path = directory / "run.json"
    if args.resume:
        manifest = json.loads(path.read_bytes())
        if ((args.through and str(args.through) != manifest["through"])
                or args.port != manifest["port"] or args.volume_unit != manifest["volume_unit"]
                or tuple(manifest["tickers"]) != TICKERS or manifest["floor"] != str(FLOOR)):
            raise ValueError("Resume settings must match the frozen run.")
        return manifest
    now = datetime.now(timezone.utc)
    latest = cutoff_at(now)
    through = args.through or latest
    if through < FLOOR or through > latest or not sessions(through, through):
        raise ValueError("Cutoff must be a completed NYSE session at or after the floor.")
    manifest = {"tickers": list(TICKERS), "floor": str(FLOOR), "through": str(through), "port": args.port,
                "volume_unit": args.volume_unit, "started_at": now.isoformat(),
                "inception": {ticker: {"date": str(day), "source": url} for ticker, (day, url) in INCEPTION.items()},
                "protected": {str(p): sha256_file(p) if p.exists() else None for p in PROTECTED}}
    atomic_json(path, manifest)
    return manifest


def collect_daily(archive, through):
    folder = archive.path.parent / "daily"
    folder.mkdir(exist_ok=True)
    dates = [session.date for session in sessions(FLOOR, through)]
    for ticker in TICKERS:
        for delay in (0, 2, 8):
            rows = archive.responses(ticker, "yahoo", "daily")
            if rows and rows[-1]["status"] in {"complete", "empty"}:
                break
            if delay:
                sleep(delay)
            archive.save(ticker, "yahoo", "daily", download_daily(ticker, FLOOR, through))
        result = archive.responses(ticker, "yahoo", "daily")[-1]
        if "csv" in result:
            (folder / f"{ticker}.provider.csv").write_text(result["csv"], encoding="utf-8", newline="")
        snapshot, _ = daily_snapshot(ticker, dates, result)
        atomic_json(folder / f"{ticker}.json", snapshot)
        print(f"Yahoo {ticker}: {result['status']} {result.get('detail', '')}", flush=True)


def fetch_is_terminal(result, window, duration):
    status = result["status"]
    if status in {"empty", "entitlement"}:
        return True
    if status != "complete" or duration != "1 D":
        return status == "complete"
    day = window[0].date
    evidence = {day: SessionEvidence(window[0])}
    add_response(evidence, "response", result, [])
    return evidence[day].verified


def pull(archive, client, identity, window, duration, key, attempts=2, retry_entitlements=False):
    ticker = identity["ticker"]
    prior = archive.responses(ticker, "tws", key)
    retry_access = prior and prior[-1]["status"] == "entitlement" and retry_entitlements
    if prior and not retry_access and fetch_is_terminal(prior[-1], window, duration):
        return prior
    remaining = 1 if retry_access else attempts - sum(row["status"] not in RECOVER_CONNECTION for row in prior)
    for number in range(remaining):
        if number:
            client.wait(3)
        result = client.fetch(identity, window, duration)
        archive.save(ticker, "tws", key, result)
        prior.append(result)
        if result["status"] in RECOVER_CONNECTION:
            raise ConnectionError(f"{ticker} {key}: {result['status']} {result.get('detail', '')}")
        if fetch_is_terminal(result, window, duration):
            break
    return prior


def _collect_group(archive, window, port, client_id, symbols, wait_for_slot, inception_dates, retry_entitlements):
    years = [(str(key), list(group)) for key, group in groupby(window, key=lambda s: s.date.year)]
    for retry, delay in enumerate((0, 30, 90)):
        if delay:
            sleep(delay)
        try:
            with EquityHistory(port, client_id, 25, wait_for_slot=wait_for_slot) as client:
                identities = {}
                for ticker in symbols:
                    prior = archive.responses(ticker, "tws_meta", "contract")
                    try:
                        identity = client.qualify(ticker)
                    except Exception as error:
                        archive.save(ticker, "tws_meta", "contract_error", {"status": "unresolved", "detail": str(error)})
                        print(f"TWS {ticker}: qualification error {error}", flush=True)
                        continue
                    if prior and identity != prior[-1]:
                        raise ValueError(f"{ticker} contract changed since this run started.")
                    if not prior:
                        archive.save(ticker, "tws_meta", "contract", identity)
                    identities[ticker] = identity
                blocked = set()
                for year, year_window in reversed(years):
                    for ticker, identity in identities.items():
                        if ticker in blocked:
                            continue
                        inception = inception_dates.get(ticker, FLOOR)
                        wanted = [s for s in year_window if s.date >= inception]
                        if not wanted:
                            continue
                        evidence = {s.date: SessionEvidence(s) for s in wanted}
                        print(f"TWS {ticker} {year}: checking history", flush=True)
                        annual = pull(archive, client, identity, wanted, "12 M", year, attempts=2,
                                      retry_entitlements=retry_entitlements)
                        if annual[-1]["status"] == "entitlement":
                            blocked.add(ticker)
                            continue
                        for i, response in enumerate(annual, 1):
                            add_response(evidence, f"{year}#{i}", response, [])
                        for month, group in groupby(wanted, key=lambda s: str(s.date)[:7]):
                            missing = [s for s in group if not evidence[s.date].verified and not evidence[s.date].conflicts]
                            if not missing:
                                continue
                            monthly = pull(archive, client, identity, missing, "1 M", month,
                                           retry_entitlements=retry_entitlements)
                            for i, response in enumerate(monthly, 1):
                                add_response(evidence, f"{month}#{i}", response, [])
                            if monthly[-1]["status"] == "entitlement":
                                blocked.add(ticker)
                                break
                        if ticker in blocked:
                            continue
                        for day, item in evidence.items():
                            if not item.verified and not item.conflicts:
                                replies = pull(archive, client, identity, [item.session], "1 D", str(day),
                                               retry_entitlements=retry_entitlements)
                                for i, response in enumerate(replies, 1):
                                    add_response(evidence, f"{day}#{i}", response, [])
                                if replies[-1]["status"] == "entitlement":
                                    blocked.add(ticker)
                                    break
                        verified = sum(item.verified for item in evidence.values())
                        print(f"TWS {ticker} {year}: {verified}/{len(wanted)} complete sessions", flush=True)
                return
        except (ConnectionError, OSError, TimeoutError) as error:
            print(f"TWS connection attempt {retry + 1}: {error}", flush=True)
    raise ConnectionError("TWS did not recover after bounded retries. The archived run can resume.")


def collect_intraday(archive, window, port, client_id, workers, inception_dates, retry_entitlements):
    lock = Lock()
    next_start = 0.0

    def wait_for_slot():
        nonlocal next_start
        with lock:
            sleep(max(0, next_start - monotonic()))
            next_start = monotonic() + 2.0

    def work(index):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            _collect_group(archive, window, port, client_id + index, TICKERS[index::workers], wait_for_slot,
                           inception_dates, retry_entitlements)
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.submit(work, i) for i in range(workers))
        for future in results:
            future.result()


def audit(archive, root, window, manifest, inception_dates, publish):
    report = []
    replay = root / "replay"
    protected_after = {str(p): sha256_file(p) if p.exists() else None for p in PROTECTED}
    if manifest["protected"] != protected_after:
        raise ValueError("A pre-existing cache changed since run creation. Review the hashes before using the audit.")
    if publish:
        replay.mkdir(exist_ok=True)
    staged_audit = Path(mkdtemp(prefix=".audit-", dir=root))
    with ExitStack() as stack:
        writers = setup_writers(stack, staged_audit)
        for ticker in TICKERS:
            dates = [s.date for s in window]
            raw_yahoo = archive.responses(ticker, "yahoo", "daily")
            daily_input = raw_yahoo[-1] if raw_yahoo else {"status": "unattempted"}
            daily, daily_flags = daily_snapshot(ticker, dates, daily_input)
            evidence = {s.date: SessionEvidence(s) for s in window}
            anomalies = []
            for reference, response in archive.requests(ticker, "tws"):
                add_response(evidence, reference, response, anomalies)
            contracts = archive.responses(ticker, "tws_meta", "contract")
            identity = contracts[-1] if contracts else None

            def release(block):
                if identity is None:
                    raise ValueError("No qualified contract.")
                return publication_bundle(replay, ticker, identity, manifest["volume_unit"], block, daily, evidence)

            summary, receipt = audit_symbol(ticker, window, inception_dates.get(ticker), daily, daily_flags,
                                            daily_input["status"], evidence, anomalies, writers, release if publish else None)
            report.append({"summary": summary, "contract": identity, "publication": receipt})
            print(f"Audit {ticker}: {summary['publication_status']}, {summary['verified_sessions']} verified sessions", flush=True)
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    audit_path = root / ("audit" if publish and not (root / "audit").exists() else f"audit-{suffix}")
    hashes = {str((audit_path / p.name).relative_to(root)): sha256_file(p) for p in staged_audit.glob("*.csv")}
    paths = list((root / "daily").glob("*")) + [archive.path, root / "run.json"]
    hashes.update({str(p.relative_to(root)): sha256_file(p) for p in paths})
    record = {"run": manifest, "symbols": report, "hashes": hashes, "audit_directory": audit_path.name,
              "publication_complete": publish}
    atomic_json(staged_audit / "manifest.json", record)
    staged_audit.rename(audit_path)
    if publish:
        atomic_json(root / "manifest.json", record)
    print(f"Audit ledgers: {audit_path}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--port", type=int, default=7497)
    parser.add_argument("--client-id", type=int, default=186)
    parser.add_argument("--workers", type=int, choices=range(1, 7), default=3)
    parser.add_argument("--retry-entitlements", action="store_true")
    parser.add_argument("--volume-unit", choices=("shares", "lots"), required=True)
    parser.add_argument("--through", type=date.fromisoformat)
    parser.add_argument("--stage", choices=("all", "daily", "intraday", "audit"), default="all")
    args = parser.parse_args(argv)
    root = args.run_dir.resolve()
    allowed = Path("data/etf_history").resolve()
    if root.parent != allowed:
        parser.error("--run-dir must name one run directly beneath data/etf_history/.")
    try:
        if not args.resume:
            root.mkdir(parents=True)
        with FileLock(str(root / "run.lock"), timeout=0):
            manifest = run_arguments(args, root)
            through = date.fromisoformat(manifest["through"])
            inception_dates = {ticker: date.fromisoformat(item["date"]) for ticker, item in manifest["inception"].items()}
            archive = Archive(root)
            if (root / "manifest.json").exists() and args.stage != "audit":
                raise ValueError("This run is published. Use a new run directory for further provider collection.")
            print(f"Run {root.name} | {FLOOR} through {through} | {args.stage}", flush=True)
            if args.stage in {"daily", "all"}:
                collect_daily(archive, through)
            window = sessions(FLOOR, through)
            if args.stage in {"intraday", "all"}:
                collect_intraday(archive, window, args.port, args.client_id, args.workers, inception_dates,
                                 args.retry_entitlements)
                atomic_json(root / "intraday-complete.json", {"finished_at": datetime.now(timezone.utc).isoformat()})
            if args.stage in {"audit", "all"}:
                audit(archive, root, window, manifest, inception_dates, (root / "intraday-complete.json").is_file())
    except (ValueError, OSError, TimeoutError) as error:
        print(f"ETF acquisition: {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
