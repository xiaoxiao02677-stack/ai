"""SQLite storage provider: connection, schema, raw primitives.

Phase-1A refactor: extracted verbatim from store.py (connection setup,
table DDL, thread lock, row-to-record mapping helpers). Phase-1B promotes
that implicit surface into the explicit ``StorageProvider`` Protocol
(``provider.py``) and stops repositories from touching ``_lock``/``_conn``
directly: they now go through ``execute`` / ``query_one`` / ``query_rows``
/ ``transaction``.

Owns *everything* database-specific: connection, DDL, locking, commit
boundaries, sqlite3 objects. Knows nothing about domain logic (dedup /
conflict / retrieval).

One DB file per conf_uid under <cwd>/long_term_memory_data/. stdlib
sqlite3 only. Thread-safety: a single lock serializes access; the single
connection is shared with check_same_thread=False.

This is the SQLite implementation of ``StorageProvider``. A future Hermes
provider implements the same Protocol and can be swapped in without any
repository change.
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence

from loguru import logger

from ..schemas import MemoryRecord

_DATA_DIR = os.path.join(os.getcwd(), "long_term_memory_data")


class _SQLiteTransaction:
    """``StorageTransaction`` backed by an already-entered sqlite3 connection.

    The connection's own context manager has already begun the transaction
    (commit on clean exit, rollback on exception), so this handle only
    forwards statements — it does not manage commit/rollback itself.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        return self._conn.execute(sql, params).rowcount

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchone()

    def query_rows(self, sql: str, params: Sequence[Any] = ()) -> Sequence[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchall()


class SQLiteStorageProvider:
    """Low-level SQLite persistence: connection, schema, primitive access.

    Implements the ``StorageProvider`` Protocol from ``provider.py``.
    """

    def __init__(self, conf_uid: str):
        self.conf_uid = conf_uid
        os.makedirs(_DATA_DIR, exist_ok=True)
        safe = "".join(c for c in conf_uid if c.isalnum() or c in "-_")
        self.db_path = os.path.join(_DATA_DIR, f"{safe}.db")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    # -- schema ---------------------------------------------------------------

    def _init_tables(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    conf_uid TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    keywords TEXT NOT NULL DEFAULT '[]',
                    source_history_uid TEXT DEFAULT '',
                    importance REAL DEFAULT 0.5,
                    confidence REAL DEFAULT 0.8,
                    status TEXT DEFAULT 'active',
                    use_count INTEGER DEFAULT 0,
                    created_at REAL,
                    updated_at REAL,
                    last_used_at REAL,
                    history TEXT DEFAULT '[]'
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mem_conf ON memories(conf_uid, status)"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS keywords (
                    keyword TEXT NOT NULL,
                    category TEXT NOT NULL,
                    conf_uid TEXT NOT NULL,
                    hit_count INTEGER DEFAULT 1,
                    first_seen_at REAL,
                    last_seen_at REAL,
                    PRIMARY KEY (keyword, category, conf_uid)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_state (
                    conf_uid TEXT PRIMARY KEY,
                    emotion TEXT,
                    energy REAL,
                    stress REAL,
                    current_topic TEXT,
                    intent TEXT,
                    updated_at REAL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS summary (
                    conf_uid TEXT PRIMARY KEY,
                    conversation_summary TEXT DEFAULT '',
                    turn_count INTEGER DEFAULT 0,
                    updated_at REAL
                )
                """
            )

    # -- write primitives -----------------------------------------------------

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a single write statement in its own transaction."""
        with self._lock, self._conn:
            return self._conn.execute(sql, params).rowcount

    # -- read primitives ------------------------------------------------------

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        """Return the first matching row, or ``None``."""
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def query_rows(self, sql: str, params: Sequence[Any] = ()) -> Sequence[sqlite3.Row]:
        """Return all matching rows."""
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # -- transactional scope --------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[_SQLiteTransaction]:
        """Open an atomic write scope mirroring the old ``with lock, conn:``.

        Acquires the lock, enters the connection's transaction (commit on
        clean exit, rollback on exception), and yields a statement handle.
        """
        with self._lock, self._conn:
            yield _SQLiteTransaction(self._conn)

    # -- row mapping ----------------------------------------------------------

    @staticmethod
    def row_to_memory_record(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            memory_id=row["memory_id"],
            conf_uid=row["conf_uid"],
            memory_type=row["memory_type"],
            content=row["content"],
            keywords=json.loads(row["keywords"] or "[]"),
            source_history_uid=row["source_history_uid"] or "",
            importance=float(row["importance"] or 0.5),
            confidence=float(row["confidence"] or 0.8),
            status=row["status"] or "active",
            use_count=int(row["use_count"] or 0),
            created_at=float(row["created_at"] or 0.0),
            updated_at=float(row["updated_at"] or 0.0),
            last_used_at=float(row["last_used_at"] or 0.0),
            history=json.loads(row["history"] or "[]"),
        )

    # -- lifecycle ------------------------------------------------------------

    def close(self) -> None:
        try:
            with self._lock:
                self._conn.close()
        except Exception as e:
            logger.warning(f"[LTM] store close error: {e}")
