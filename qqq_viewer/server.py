import argparse
import json
import re
import sqlite3
from collections import deque
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .sessions import NEW_YORK, START_DATE, sessions
from .store import BAR_COLUMNS, Bar, Store

STATIC = Path(__file__).with_name("static")
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/style.css": ("style.css", "text/css; charset=utf-8"),
    "/static/chart.js": ("chart.js", "application/javascript; charset=utf-8"),
    "/static/vendor/lightweight-charts.js": ("vendor/lightweight-charts.js", "application/javascript"),
}
MONTH = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])", flags=re.ASCII)


def _bar_date(epoch: int) -> date:
    return datetime.fromtimestamp(epoch, NEW_YORK).date()


def _next_month(month: date) -> date:
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1)


def _latest_date(db: sqlite3.Connection) -> date | None:
    value = db.execute("SELECT MAX(time) FROM bars").fetchone()[0]
    return _bar_date(value) if value is not None else None


def month_index(store: Store) -> dict:
    if not store.path.is_file():
        return {"months": [], "default_month": None}
    with store.reader() as db:
        latest = _latest_date(db)
        cutoff = store.target_end_date(db) or latest
    months = []
    month = START_DATE
    while cutoff and month <= cutoff:
        months.append(month.strftime("%Y-%m"))
        month = _next_month(month)
    return {"months": months, "default_month": latest.strftime("%Y-%m") if latest else None}


def month_payload(store: Store, month: str) -> dict:
    if not MONTH.fullmatch(month) or not "2020-01" <= month < "9999-12":
        raise ValueError("Enter a month from January 2020 onward.")
    first = date.fromisoformat(month + "-01")
    following = _next_month(first)
    start = int(datetime.combine(first, time.min, NEW_YORK).timestamp())
    end = int(datetime.combine(following, time.min, NEW_YORK).timestamp())
    rows = []
    expected = unverified = 0
    conflict_times = []
    month_sessions = []
    unit = None
    if store.path.is_file():
        with store.reader() as db:
            target = store.target_end_date(db) or _latest_date(db)
            if target and first <= target:
                month_sessions = sessions(first, min(target, following - timedelta(days=1)))
            expected = sum(len(session.minutes) for session in month_sessions)
            unverified = sum(not store.has_completed_response(db, session) for session in month_sessions)
            warmup = db.execute("SELECT volume FROM bars WHERE time<? ORDER BY time DESC LIMIT 19", (start,))
            window = deque((r[0] for r in reversed(warmup.fetchall())), maxlen=20)
            session_date = None
            session_volume = weighted_price = 0.0
            for row in db.execute(
                f"SELECT {BAR_COLUMNS} FROM bars WHERE time>=? AND time<? ORDER BY time", (start, end),
            ):
                bar = Bar(*row)
                bar_date = _bar_date(bar.time)
                if bar_date != session_date:
                    session_date = bar_date
                    session_volume = weighted_price = 0.0
                typical_price = (bar.high + bar.low + bar.close) / 3
                session_volume += bar.volume
                weighted_price += typical_price * bar.volume
                window.append(bar.volume)
                rows.append(asdict(bar) | {
                    "volume_sma_20": sum(window) / 20 if len(window) == 20 else None,
                    "session_vwap": weighted_price / session_volume if session_volume else None,
                })
            conflict_times = [row[0] for row in db.execute(
                "SELECT DISTINCT time FROM conflicts WHERE time>=? AND time<? ORDER BY time", (start, end),
            )]
            volume_unit = db.execute("SELECT value FROM metadata WHERE key='volume_unit'").fetchone()
            unit = volume_unit[0] if volume_unit else None
    missing = max(0, expected - len(rows))
    coverage_status = "empty" if not rows else "incomplete" if missing or unverified else "complete"
    return {
        "month": month, "bars": rows, "conflict_times": conflict_times,
        "sessions": [{"date": s.date.isoformat(), "open_utc": s.open_utc, "close_utc": s.close_utc}
                     for s in month_sessions],
        "coverage": {"expected": expected, "received": len(rows), "missing": missing,
                     "conflicting": len(conflict_times), "unverified_sessions": unverified, "status": coverage_status},
        "volume_unit": unit,
    }


def make_server(db_path: Path, port: int) -> ThreadingHTTPServer:
    store = Store(db_path, read_only=True)

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status: int, content_type: str, body: bytes):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def json(self, status: int, value: dict):
            self.respond(status, "application/json; charset=utf-8",
                         json.dumps(value, separators=(",", ":"), allow_nan=False).encode())

        def do_GET(self):
            path = urlsplit(self.path).path
            try:
                if path in ASSETS:
                    name, content_type = ASSETS[path]
                    self.respond(200, content_type, (STATIC / name).read_bytes())
                elif path == "/api/months":
                    self.json(200, month_index(store))
                elif path.startswith("/api/month/"):
                    self.json(200, month_payload(store, path.removeprefix("/api/month/")))
                else:
                    self.json(404, {"error": "Page not found."})
            except ValueError as exc:
                self.json(400, {"error": str(exc)})
            except (OSError, sqlite3.Error) as exc:
                self.log_error("%s", exc)
                self.json(503, {"error": "The local viewer could not read its cache or chart assets."})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description="Serve the local QQQ one-minute viewer.")
    parser.add_argument("--db", type=Path, default=Path("data/qqq.sqlite3"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with make_server(args.db, args.port) as server:
        print(f"QQQ viewer at http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
