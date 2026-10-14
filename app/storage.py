from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_RETENTION_SECONDS = 3600


def retention_seconds(value: float | None = None) -> float:
    duration = float(os.environ.get("FLAG_RETENTION_SECONDS", DEFAULT_RETENTION_SECONDS) if value is None else value)
    if not 1 <= duration <= 30 * 24 * 3600:
        raise ValueError("FLAG_RETENTION_SECONDS must be between 1 second and 30 days")
    return duration


@dataclass(frozen=True)
class Record:
    key: str
    owner: str
    data: bytes
    created: float


class RecordStore:

    def __init__(self, path: str | Path | None = None, *, lifetime: float | None = None) -> None:
        self.lifetime = retention_seconds(lifetime)
        self._lock = threading.RLock()
        if path is not None:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._db = sqlite3.connect(str(path) if path is not None else ":memory:", check_same_thread=False)
        if path is not None:
            os.chmod(path, 0o600)
        self._db.execute("PRAGMA busy_timeout = 5000")
        self._db.execute("PRAGMA synchronous = FULL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS records ("
            "namespace TEXT NOT NULL, key TEXT NOT NULL, owner TEXT NOT NULL, "
            "data BLOB NOT NULL, created REAL NOT NULL, expires REAL, "
            "PRIMARY KEY (namespace, key))"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS records_expiry ON records(namespace, expires)")
        self._db.commit()

    def put(self, namespace: str, key: str, owner: str, data: bytes, *,
            permanent: bool = False, create_only: bool = False) -> bool:
        now = time.time()
        expires = None if permanent else now + self.lifetime
        conflict = "DO NOTHING" if create_only else (
            "DO UPDATE SET owner=excluded.owner, data=excluded.data, "
            "created=excluded.created, expires=excluded.expires"
        )
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO records(namespace,key,owner,data,created,expires) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(namespace,key) " + conflict,
                (namespace, key, owner, data, now, expires),
            )
            return cursor.rowcount == 1

    def get(self, namespace: str, key: str, owner: str | None = None) -> Record | None:
        with self._lock:
            row = self._db.execute(
                "SELECT key,owner,data,created FROM records WHERE namespace=? AND key=? "
                "AND (expires IS NULL OR expires>?)",
                (namespace, key, time.time()),
            ).fetchone()
        if row is None or (owner is not None and row[1] != owner):
            return None
        return Record(*row)

    def active(self, namespace: str) -> list[Record]:
        with self._lock:
            rows = self._db.execute(
                "SELECT key,owner,data,created FROM records WHERE namespace=? "
                "AND (expires IS NULL OR expires>?) ORDER BY created,key",
                (namespace, time.time()),
            ).fetchall()
        return [Record(*row) for row in rows]

    def prune(self, namespace: str) -> list[Record]:
        with self._lock, self._db:
            now = time.time()
            rows = self._db.execute(
                "SELECT key,owner,data,created FROM records WHERE namespace=? AND expires<=?",
                (namespace, now),
            ).fetchall()
            self._db.execute("DELETE FROM records WHERE namespace=? AND expires<=?", (namespace, now))
        return [Record(*row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()
