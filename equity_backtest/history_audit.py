"""Session-level reconciliation and verified replay publication."""

import csv
import hashlib
import json
import sqlite3
from dataclasses import astuple, dataclass, field
from datetime import date, datetime, timezone
from itertools import groupby
from pathlib import Path
from tempfile import mkdtemp

from qqq_viewer.price_basis import price_basis_mismatch
from qqq_viewer.spy_5m_contiguous import verify_published

from .acquisition_archive import atomic_json, canonical, sha256_file
from .backtest_5m import _levels, load_settings, run
from .history_5m import FiveMinuteBar, FiveMinuteStore, expected_times, normalize_bar, sessions, usable_session
from .market_time import NEW_YORK, Session
from .snapshot import close_wal


@dataclass
class SessionEvidence:
    session: Session
    bars: dict = field(default_factory=dict)
    conflicts: set = field(default_factory=set)
    attempts: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    proof: str | None = None

    @property
    def verified(self):
        return self.proof is not None and not self.conflicts

    def add(self, status, bars, reference):
        self.attempts.append((status, reference))
        confirmed = {}
        grid = expected_times(self.session)
        for bar in bars:
            stamp = bar.time
            clean = normalize_bar(bar) if stamp in grid else None
            if clean is None:
                self.issues.append((stamp, "invalid_or_off_grid", reference))
                continue
            if stamp in confirmed:
                self.issues.append((stamp, "duplicate_in_response", reference))
            prior = self.bars.get(stamp)
            if prior is None:
                self.bars[stamp] = clean
            elif astuple(prior)[:6] != astuple(clean)[:6] or (
                prior.wap is not None and clean.wap is not None and prior.wap != clean.wap
            ):
                self.conflicts.add(stamp)
                self.issues.append((stamp, "conflict", reference))
                continue
            elif prior.wap is None and clean.wap is not None:
                self.bars[stamp] = clean
            if clean.volume and clean.wap is None:
                self.issues.append((stamp, "positive_volume_without_wap", reference))
            confirmed[stamp] = clean
        if status == "complete" and usable_session(self.session, list(confirmed.values())):
            self.proof = self.proof or reference


def add_response(evidence, reference, response, anomalies):
    eligible = sessions(date.fromisoformat(response["start"]), date.fromisoformat(response["end"]))
    by_date = {session.date: [] for session in eligible if session.date in evidence}
    for index, raw in enumerate(response["rows"]):
        try:
            stamp, _, o, h, l, c, v, wap = raw
            day = datetime.fromtimestamp(stamp, NEW_YORK).date()
            bar = FiveMinuteBar(stamp, *map(float, (o, h, l, c, v)), None if wap == "None" else float(wap))
        except (TypeError, ValueError, OverflowError, OSError):
            anomalies.append(("", "", "invalid_tws_row", f"{reference}:{index}"))
            continue
        if day not in by_date:
            anomalies.append((str(day), stamp, "outside_requested_session_dates", f"{reference}:{index}"))
            continue
        by_date[day].append(bar)
    for day, bars in by_date.items():
        evidence[day].add(response["status"], bars, reference)


def verified_blocks(window, verified):
    return [(block[0].date, block[-1].date) for allowed, group in groupby(window, key=lambda s: s.date in verified)
            if allowed and (block := list(group))]


def publication_bundle(root, ticker, identity, volume_unit, block, daily, evidence):
    destination = root / ticker
    start, end = map(str, block)
    target = sessions(*block)
    daily_rows = {row["time"]: row for row in daily["bars"]}
    eligible = 0
    digest = hashlib.sha256(canonical([identity, volume_unit, daily, start, end]))
    for session in target:
        item = evidence[session.date]
        upper, lower, _ = _levels(daily_rows.get(str(session.date)), item.bars[session.open_utc].open)
        eligible += upper is not None and lower is not None
        digest.update(canonical([item.proof, [astuple(item.bars[t]) for t in expected_times(session)]]))
    fingerprint = digest.hexdigest()
    if not eligible:
        raise ValueError("No dates in the verified block have usable paired Yahoo daily levels.")
    if destination.exists():
        receipt = json.loads((destination / "manifest.json").read_bytes())
        if receipt.get("source_sha256") != fingerprint or any(
                sha256_file(destination / name) != digest for name, digest in receipt["hashes"].items()):
            raise ValueError("Previously published bundle differs from this audit.")
        return receipt
    staged = Path(mkdtemp(prefix=f".{ticker}-", dir=root))
    cache = staged / "intraday.sqlite3"
    store = FiveMinuteStore(cache)
    store.configure(identity["con_id"], volume_unit)
    store.set_metadata("ticker", ticker)
    with store._connection(write=True) as db:
        for session in target:
            day = str(session.date)
            item = evidence[session.date]
            db.executemany("INSERT INTO bars VALUES (?,?,?,?,?,?,?,?)", ((*astuple(bar), day) for bar in item.bars.values()))
            db.execute("INSERT INTO sessions VALUES (?,1,1)", (day,))
            db.execute("INSERT INTO attempts(date,status,diagnostic,accepted) VALUES (?,'complete',?,?)",
                       (day, item.proof, len(item.bars)))
        db.executemany("INSERT INTO metadata VALUES (?,?)", (("verified_contiguous_start", start),
                       ("verified_contiguous_end", end), ("target", end)))
    close_wal(cache)
    verified = verify_published(store)
    atomic_json(staged / "daily.json", daily)
    config = staged / "config.toml"
    config.write_text(f'ticker = "{ticker}"\ncontract_id = {identity["con_id"]}\n'
                      'intraday_cache = "intraday.sqlite3"\ndaily_cache = "daily.json"\n\n'
                      '[exits]\nsame_day_close = false\ntrailing_stop = true\nexit4 = true\nexit5 = true\n', encoding="utf-8")
    smoke = run(load_settings(config), block[0], block[0])
    if smoke["ticker"] != ticker or smoke["coverage"]["start"] != start:
        raise ValueError("Replay smoke check disagrees with the published identity or start.")
    hashes = {name: sha256_file(staged / name) for name in ("intraday.sqlite3", "daily.json", "config.toml")}
    receipt = {"ticker": ticker, "start": start, "end": end, "sessions": verified["sessions"], "bars": verified["bars"],
               "hashes": hashes, "config": str(destination / "config.toml"), "source_sha256": fingerprint,
               "sessions_with_daily_levels": eligible}
    atomic_json(staged / "manifest.json", receipt)
    staged.rename(destination)
    return receipt


AUDIT_HEADERS = {
    "sessions": ("ticker", "date", "daily_status", "intraday_status", "expected_bars", "usable_bars", "verified_bars", "attempts"),
    "missing_5m_bars": ("ticker", "date", "utc", "new_york", "status"),
    "anomalies": ("ticker", "date", "utc_epoch", "type", "evidence"),
    "verified_blocks": ("ticker", "start", "end", "sessions", "bars", "publication_candidate"),
    "symbols": ("ticker", "daily_rows", "daily_gaps", "verified_sessions", "expected_sessions", "missing_bars",
                "verified_blocks", "published_start", "published_end", "tail_sessions", "publication_status", "session_statuses"),
}


def audit_symbol(ticker, window, inception, daily, daily_flags, daily_outcome, evidence, anomalies, writers, release):
    from collections import Counter

    daily_rows = {row["time"]: row for row in daily["bars"]}
    counts = Counter()
    verified = set()
    daily_gaps = missing_total = 0
    for timestamp, reason, reference in daily_flags:
        writers["anomalies"].writerow((ticker, timestamp, "", reason, reference))
    for day, stamp, reason, reference in anomalies:
        writers["anomalies"].writerow((ticker, day, stamp, reason, reference))
    for session in window:
        day = str(session.date)
        item = evidence[session.date]
        grid = expected_times(session)
        if inception and session.date < inception and day not in daily_rows and not item.bars:
            counts["pre_inception"] += 1
            writers["sessions"].writerow((ticker, day, "pre_inception", "pre_inception", 0, 0, 0, ""))
            continue
        for stamp, reason, reference in item.issues:
            writers["anomalies"].writerow((ticker, day, stamp, reason, reference))
        daily_status = "present" if day in daily_rows else ("missing" if daily_outcome == "complete" else daily_outcome)
        daily_gaps += day not in daily_rows
        if item.verified:
            opening, close = item.bars[grid[0]].open, item.bars[grid[-1]].close
            row = daily_rows.get(day, {})
            basis = price_basis_mismatch(opening, close, row)
            if basis:
                status = "price_basis_mismatch"
                writers["anomalies"].writerow((ticker, day, "", status, f"TWS {opening}/{close}; Yahoo {row['open']}/{row['close']}"))
            else:
                status = "verified"
                verified.add(session.date)
        elif item.conflicts:
            status = "conflict"
        elif item.bars:
            status = "partial_or_unverified"
        elif not item.attempts:
            status = "unattempted"
        elif item.attempts[-1][0] == "empty":
            status = "empty"
        elif item.attempts[-1][0] == "complete":
            status = "omitted_or_invalid"
        else:
            status = item.attempts[-1][0]
        counts[status] += 1
        usable = {stamp for stamp, bar in item.bars.items() if not bar.volume or bar.wap is not None}
        missing = set(grid) - usable
        missing_total += len(missing)
        writers["sessions"].writerow((ticker, day, daily_status, status, len(grid), len(usable),
                                     len(grid) if status == "verified" else 0,
                                     "|".join(f"{s}:{r}" for s, r in item.attempts)))
        for stamp in sorted(missing):
            utc = datetime.fromtimestamp(stamp, timezone.utc)
            writers["missing_5m_bars"].writerow((ticker, day, utc.isoformat(), utc.astimezone(NEW_YORK).isoformat(), status))
    blocks = verified_blocks(window, verified)
    for block in blocks:
        grouped = sessions(*block)
        writers["verified_blocks"].writerow((ticker, *block, len(grouped), sum(len(expected_times(day)) for day in grouped), block == blocks[-1]))
    receipt, message = None, "no_verified_block"
    if blocks and release is None:
        message = "audit_only"
    elif blocks:
        try:
            receipt = release(blocks[-1])
            message = "published"
        except (ValueError, OSError, sqlite3.Error) as error:
            message = f"withheld: {error}"
    summary = (ticker, len(daily_rows), daily_gaps, len(verified), len(window) - counts["pre_inception"], missing_total,
               len(blocks), receipt["start"] if receipt else "", receipt["end"] if receipt else "",
               sum(s.date > blocks[-1][1] for s in window) if blocks else "", message, dict(counts))
    writers["symbols"].writerow(summary)
    return dict(zip(AUDIT_HEADERS["symbols"], summary, strict=True)), receipt


def setup_writers(stack, directory):
    directory.mkdir(parents=True, exist_ok=True)
    writers = {}
    for name, header in AUDIT_HEADERS.items():
        stream = stack.enter_context((directory / f"{name}.csv").open("w", newline="", encoding="utf-8"))
        writer = csv.writer(stream)
        writer.writerow(header)
        writers[name] = writer
    return writers
