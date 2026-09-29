"""SQLite storage provider: connection, schema, raw row access.

Phase-1A refactor: extracted verbatim from store.py (connection setup,
table DDL, thread lock, row-to-record mapping helpers). Owns *how* data
is persisted; knows nothing about domain logic (dedup/conflict/retrieval).

One DB file per conf_uid under <cwd>/long_term_memory_data/. stdlib
sqlite3 only. Thread-safety: a single lock serializes writes; reads use
the same connection guarded by check_same_thread=False.

This is the SQLite implementation of the StorageProvider role. A future
Hermes provider must implement the same surface used by the repositories
(connection lifecycle + execute helpers), NOT this class hierarchy —
see storage/README notes in AI_MODULE_README.
"""

import json
import os
import sqlite3
import threading
from typing import List, Optional

from loguru import logger

from ..schemas import MemoryRecord

_DATA_DIR = os.path.join(os.getcwd(), "long_term_memory_data")


class SQLiteStorageProvider:
    """Low-level SQLite persistence: connection, schema, row mapping."""

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
