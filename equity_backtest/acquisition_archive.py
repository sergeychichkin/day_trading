"""Durable provider responses. A committed response is its own checkpoint."""

import hashlib
import json
import os
import sqlite3
import zlib
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def atomic_json(path: Path, value):
    with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        name = Path(temporary.name)
        try:
            temporary.write(canonical(value))
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary.close()
            name.replace(path)
        finally:
            temporary.close()
            name.unlink(missing_ok=True)


def sha256_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class Archive:
    def __init__(self, directory):
        self.path = directory / "provider.sqlite3"
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS responses ("
                       "symbol TEXT, source TEXT, request TEXT, attempt INTEGER, sha256 TEXT, payload BLOB, "
                       "PRIMARY KEY(symbol,source,request,attempt))")
            db.commit()

    def _connect(self):
        return closing(sqlite3.connect(self.path, timeout=30))

    def save(self, symbol, source, request, value):
        raw = canonical(value)
        with self._connect() as db, db:
            db.execute("BEGIN IMMEDIATE")
            attempt = db.execute("SELECT coalesce(max(attempt),0)+1 FROM responses "
                                 "WHERE symbol=? AND source=? AND request=?", (symbol, source, request)).fetchone()[0]
            db.execute("INSERT INTO responses VALUES (?,?,?,?,?,?)",
                       (symbol, source, request, attempt, hashlib.sha256(raw).hexdigest(), zlib.compress(raw)))
        return attempt

    @staticmethod
    def _decode(digest, payload):
        raw = zlib.decompress(payload)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Provider archive hash mismatch.")
        return json.loads(raw)

    def responses(self, symbol, source, request):
        with self._connect() as db:
            rows = db.execute("SELECT sha256,payload FROM responses WHERE symbol=? AND source=? AND request=? "
                              "ORDER BY attempt", (symbol, source, request))
            return [self._decode(*row) for row in rows]

    def requests(self, symbol, source):
        with self._connect() as db:
            rows = db.execute("SELECT request,attempt,sha256,payload FROM responses WHERE symbol=? AND source=? "
                              "ORDER BY request,attempt", (symbol, source))
            for request, attempt, digest, payload in rows:
                yield f"{request}#{attempt}", self._decode(digest, payload)
