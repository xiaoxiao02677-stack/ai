"""SQLite storage provider: connection, schema, and ALL SQL in the system.

Phase-2 refactor: the SQL that Phase-1B left inside the four repositories
(INSERT/SELECT/UPDATE strings, transaction plumbing) sinks entirely into
this file. The provider now implements the aggregate-shaped
``StorageProvider`` Protocol (``provider.py``): every method takes or
returns domain objects (``MemoryRecord`` / ``KeywordRecord`` /
``UserState`` / scalars), and row <-> object mapping is private.

Owns everything database-specific: connection, DDL, locking, commit
boundaries, sqlite3 objects, and every SQL string. Knows nothing about
domain logic (dedup / conflict / retrieval ranking).

One DB file per conf_uid under <cwd>/long_term_memory_data/. stdlib
sqlite3 only. Thread-safety: a single lock serializes access; the single
connection is shared with check_same_thread=False.

This is the SQLite implementation of ``StorageProvider``. A future Hermes
provider implements the same Protocol and can be swapped in at the
composition root (``store.py``) without touching anything else.

DDL note: the four CREATE TABLE statements and the index are byte-identical
to every phase since the original monolith — existing .db files keep
working with zero migration.
"""

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator, List, Optional, Sequence

from loguru import logger

from ..schemas import KeywordRecord, MemoryRecord, UserState

_DATA_DIR = os.path.join(os.getcwd(), "long_term_memory_data")


class SQLiteStorageProvider:
    """SQLite persistence for the whole long-term-memory aggregate set.

    Implements the ``StorageProvider`` Protocol from ``provider.py``.
    """

    def __init__(self, conf_uid: str):
        self.conf_uid = conf_uid
        os.makedirs(_DATA_DIR, exist_ok=True)
        safe = "".join(c for c in conf_uid if c.isalnum() or c in "-_")
        self.db_path = os.path.join(_DATA_DIR, f"{safe}.db")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        # wait (up to 10s) instead of instantly failing when a second
        # connection (management panel vs. conversation loop) holds the
        # write lock — the single-connection fast path is unaffected
        self._conn.execute("PRAGMA busy_timeout = 10000")
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

    # -- private row mappers ----------------------------------------------------

    @staticmethod
    def _memory_from_row(row: sqlite3.Row) -> MemoryRecord:
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

    @staticmethod
    def _state_from_row(row: sqlite3.Row) -> UserState:
        return UserState(
            conf_uid=row["conf_uid"],
            emotion=row["emotion"] or "neutral",
            energy=float(row["energy"] or 0.5),
            stress=float(row["stress"] or 0.3),
            current_topic=row["current_topic"] or "",
            intent=row["intent"] or "chat",
            updated_at=float(row["updated_at"] or 0.0),
        )

    # -- memories aggregate ----------------------------------------------------

    def save_memory(self, record: MemoryRecord) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO memories
                (memory_id, conf_uid, memory_type, content, keywords,
                 source_history_uid, importance, confidence, status,
                 use_count, created_at, updated_at, last_used_at, history)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.memory_id,
                    record.conf_uid,
                    record.memory_type,
                    record.content,
                    json.dumps(record.keywords, ensure_ascii=False),
                    record.source_history_uid,
                    record.importance,
                    record.confidence,
                    record.status,
                    record.use_count,
                    record.created_at,
                    record.updated_at,
                    record.last_used_at,
                    json.dumps(record.history, ensure_ascii=False),
                ),
            )

    def get_memory(self, memory_id: str) -> Optional[MemoryRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memories WHERE memory_id=?", (memory_id,)
            ).fetchone()
        return self._memory_from_row(row) if row else None

    def list_memories(
        self, status: str = "active", memory_type: Optional[str] = None
    ) -> List[MemoryRecord]:
        q = "SELECT * FROM memories WHERE conf_uid=? AND status=?"
        params: list = [self.conf_uid, status]
        if memory_type:
            q += " AND memory_type=?"
            params.append(memory_type)
        q += " ORDER BY importance DESC, updated_at DESC"
        with self._lock:
            rows = self._conn.execute(q, params).fetchall()
        return [self._memory_from_row(r) for r in rows]

    def list_all_memories(self) -> List[MemoryRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE conf_uid=? "
                "ORDER BY status, importance DESC, updated_at DESC",
                (self.conf_uid,),
            ).fetchall()
        return [self._memory_from_row(r) for r in rows]

    def delete_memory(self, memory_id: str) -> bool:
        with self._lock, self._conn:
            affected = self._conn.execute(
                "DELETE FROM memories WHERE memory_id=? AND conf_uid=?",
                (memory_id, self.conf_uid),
            ).rowcount
        return affected > 0

    def find_active_by_content(self, content: str) -> Optional[MemoryRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memories WHERE conf_uid=? AND content=? AND status='active'",
                (self.conf_uid, content),
            ).fetchone()
        return self._memory_from_row(row) if row else None

    def count_memories(self, status: str = "active") -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM memories WHERE conf_uid=? AND status=?",
                (self.conf_uid, status),
            ).fetchone()
        return int(row["cnt"]) if row else 0

    def mark_used(self, memory_ids: Sequence[str]) -> None:
        now = time.time()
        with self._lock, self._conn:
            for mid in memory_ids:
                self._conn.execute(
                    "UPDATE memories SET use_count=use_count+1, last_used_at=? "
                    "WHERE memory_id=?",
                    (now, mid),
                )

    # -- keywords aggregate ----------------------------------------------------

    def upsert_keyword(self, keyword: str, category: str) -> None:
        now = time.time()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT hit_count FROM keywords "
                "WHERE keyword=? AND category=? AND conf_uid=?",
                (keyword, category, self.conf_uid),
            ).fetchone()
            if row:
                self._conn.execute(
                    "UPDATE keywords SET hit_count=hit_count+1, last_seen_at=? "
                    "WHERE keyword=? AND category=? AND conf_uid=?",
                    (now, keyword, category, self.conf_uid),
                )
            else:
                self._conn.execute(
                    "INSERT INTO keywords "
                    "(keyword, category, conf_uid, hit_count, first_seen_at, last_seen_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (keyword, category, self.conf_uid, 1, now, now),
                )

    def list_keywords(self, limit: int = 200) -> List[KeywordRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM keywords WHERE conf_uid=? "
                "ORDER BY hit_count DESC, last_seen_at DESC LIMIT ?",
                (self.conf_uid, limit),
            ).fetchall()
        return [
            KeywordRecord(
                keyword=r["keyword"],
                category=r["category"],
                conf_uid=r["conf_uid"],
                hit_count=r["hit_count"],
                first_seen_at=r["first_seen_at"],
                last_seen_at=r["last_seen_at"],
            )
            for r in rows
        ]

    def delete_keyword(self, keyword: str, category: str) -> bool:
        with self._lock, self._conn:
            affected = self._conn.execute(
                "DELETE FROM keywords WHERE keyword=? AND category=? AND conf_uid=?",
                (keyword, category, self.conf_uid),
            ).rowcount
        return affected > 0

    def count_keywords(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM keywords WHERE conf_uid=?",
                (self.conf_uid,),
            ).fetchone()
        return int(row["cnt"]) if row else 0

    # -- user state aggregate --------------------------------------------------

    def get_state(self) -> Optional[UserState]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM user_state WHERE conf_uid=?", (self.conf_uid,)
            ).fetchone()
        return self._state_from_row(row) if row else None

    def save_state(self, state: UserState) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO user_state
                (conf_uid, emotion, energy, stress, current_topic, intent, updated_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    state.conf_uid,
                    state.emotion,
                    state.energy,
                    state.stress,
                    state.current_topic,
                    state.intent,
                    state.updated_at,
                ),
            )

    # -- summary aggregate -----------------------------------------------------

    def get_summary(self) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT conversation_summary FROM summary WHERE conf_uid=?",
                (self.conf_uid,),
            ).fetchone()
        return (row["conversation_summary"] if row else "") or ""

    def save_summary(self, text: str, turn_count: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO summary
                (conf_uid, conversation_summary, turn_count, updated_at)
                VALUES (?,?,?,?)
                """,
                (self.conf_uid, text, turn_count, time.time()),
            )

    def get_turn_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT turn_count FROM summary WHERE conf_uid=?", (self.conf_uid,)
            ).fetchone()
        return int(row["turn_count"]) if row else 0

    def bump_turn_count(self) -> int:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO summary (conf_uid, conversation_summary, turn_count, updated_at)
                VALUES (?, '', 1, ?)
                ON CONFLICT(conf_uid) DO UPDATE SET turn_count = turn_count + 1, updated_at = ?
                """,
                (self.conf_uid, time.time(), time.time()),
            )
            row = self._conn.execute(
                "SELECT turn_count FROM summary WHERE conf_uid=?", (self.conf_uid,)
            ).fetchone()
            return int(row[0]) if row else 0

    # -- lifecycle -------------------------------------------------------------

    def close(self) -> None:
        try:
            with self._lock:
                self._conn.close()
        except Exception as e:
            logger.warning(f"[LTM] store close error: {e}")
