"""Serve the local SPY daily chart."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from equity_backtest.report_5m import CHART_LIBRARY

from .spy_daily import DEFAULT_CACHE as SPY_CACHE

STATIC = Path(__file__).with_name("static")
ASSETS = {
    "/spy": (STATIC / "spy.html", "text/html; charset=utf-8"),
    "/static/spy.js": (STATIC / "spy.js", "application/javascript; charset=utf-8"),
    "/static/style.css": (STATIC / "style.css", "text/css; charset=utf-8"),
    "/static/vendor/lightweight-charts.js": (CHART_LIBRARY, "application/javascript"),
}


def make_server(port: int, *, spy_cache: Path = SPY_CACHE) -> ThreadingHTTPServer:
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
                if path == "/":
                    self.send_response(302)
                    self.send_header("Location", "/spy")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif path in ASSETS:
                    asset, content_type = ASSETS[path]
                    self.respond(200, content_type, asset.read_bytes())
                elif path == "/api/spy/daily":
                    try:
                        snapshot = spy_cache.read_bytes()
                    except FileNotFoundError:
                        self.json(200, {"bars": [], "missing_sessions": [], "through": None})
                    else:
                        self.respond(200, "application/json; charset=utf-8", snapshot)
                else:
                    self.json(404, {"error": "Page not found."})
            except ValueError as exc:
                self.json(400, {"error": str(exc)})
            except OSError as exc:
                self.log_error("%s", exc)
                self.json(503, {"error": "The local viewer could not read its cache or chart assets."})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spy-cache", type=Path, default=SPY_CACHE)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with make_server(args.port, spy_cache=args.spy_cache) as server:
        print(f"SPY daily viewer at http://127.0.0.1:{server.server_port}/spy", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
