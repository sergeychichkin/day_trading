"""Read a private SQLite snapshot without creating sidecars beside the source."""

import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from .spy_5m_store import FiveMinuteStore

SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


def _file_stamp(path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_nlink


@contextmanager
def read_snapshot(path: Path):
    path = path.resolve()
    wal, journal = (Path(str(path) + suffix) for suffix in ("-wal", "-journal"))
    sources = (path, wal, journal)
    before = tuple(map(_file_stamp, sources))
    if before[0] is None:
        raise ValueError(f"SPY intraday cache not found: {path}")
    if before[0][-1] > 1:
        raise ValueError("SPY intraday cache has a hard link; use a normal SQLite copy with matching WAL files.")
    if before[2] is not None and before[2][2]:
        raise ValueError("SPY intraday cache has an active rollback journal; wait for the writer and retry.")
    with TemporaryDirectory(prefix="spy-snapshot-") as directory:
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
