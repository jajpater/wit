"""The staging index: a rebuildable SQLite cache, not the source of truth.

Per design principle (DOEL.md) ``.wit/index.sqlite`` may be deleted entirely
without losing the repository. The index accelerates `status` (change detection
on stat) and keeps track of which files are added. ``(device, inode)`` is a purely
local optimization and thus belongs here, never in a tree/commit object.
"""

from __future__ import annotations

import sqlite3
from dataclasses import astuple, dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    path      TEXT PRIMARY KEY,
    hash      TEXT    NOT NULL,
    mode      INTEGER NOT NULL,
    size      INTEGER NOT NULL,
    mtime_ns  INTEGER NOT NULL,
    ctime_ns  INTEGER NOT NULL,
    device    INTEGER NOT NULL,
    inode     INTEGER NOT NULL,
    staged    INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS conflicts (
    path TEXT PRIMARY KEY
);
"""

_UPSERT = """
INSERT INTO entries (path, hash, mode, size, mtime_ns, ctime_ns, device, inode, staged)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(path) DO UPDATE SET
    hash=excluded.hash, mode=excluded.mode, size=excluded.size,
    mtime_ns=excluded.mtime_ns, ctime_ns=excluded.ctime_ns,
    device=excluded.device, inode=excluded.inode, staged=excluded.staged
"""


@dataclass
class IndexEntry:
    path: str
    hash: str
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int
    staged: int = 1


class Index:
    def __init__(self, wit_dir: Path) -> None:
        self.path = Path(wit_dir) / "index.sqlite"
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    def __enter__(self) -> Index:
        return self

    def __exit__(self, *exc: object) -> None:
        if exc[0] is None:
            self.conn.commit()
        self.conn.close()

    def put_entry(self, entry: IndexEntry) -> None:
        self.conn.execute(_UPSERT, astuple(entry))
        # Staging a path again resolves its potential merge conflict.
        self.conn.execute("DELETE FROM conflicts WHERE path = ?", (entry.path,))

    def get(self, path: str) -> IndexEntry | None:
        row = self.conn.execute(
            "SELECT * FROM entries WHERE path = ?", (path,)
        ).fetchone()
        return IndexEntry(**dict(row)) if row else None

    def remove(self, path: str) -> None:
        self.conn.execute("DELETE FROM entries WHERE path = ?", (path,))
        self.conn.execute("DELETE FROM conflicts WHERE path = ?", (path,))

    def clear(self) -> None:
        self.conn.execute("DELETE FROM entries")

    def entries(self) -> list[IndexEntry]:
        cur = self.conn.execute("SELECT * FROM entries ORDER BY path")
        return [IndexEntry(**dict(row)) for row in cur]

    def mark_conflict(self, path: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO conflicts (path) VALUES (?)", (path,)
        )

    def conflicts(self) -> list[str]:
        cur = self.conn.execute("SELECT path FROM conflicts ORDER BY path")
        return [row["path"] for row in cur]
