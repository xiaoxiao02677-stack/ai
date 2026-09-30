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
            # Phase 4: captured interaction episodes (additive — existing
            # .db files gain the table on next open, zero data migration)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS experiences (
                    experience_id TEXT PRIMARY KEY,
                    conf_uid TEXT NOT NULL,
                    history_uid TEXT DEFAULT '',
                    interaction_type TEXT DEFAULT 'chat',
                    user_input TEXT DEFAULT '',
                    ai_response TEXT DEFAULT '',
                    tool_calls TEXT NOT NULL DEFAULT '[]',
                    outcome TEXT DEFAULT '',
                    outcome_type TEXT DEFAULT 'turn_complete',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    started_at REAL DEFAULT 0,
                    finalized_at REAL DEFAULT 0,
                    created_at REAL DEFAULT 0,
                    updated_at REAL DEFAULT 0
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_exp_conf ON experiences(conf_uid, finalized_at)"
            )
            # Phase 5: derived fact-only observations over experiences
            # (additive — existing .db files gain the table on next open)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reflections (
                    reflection_id TEXT PRIMARY KEY,
                    conf_uid TEXT NOT NULL,
                    source_experience_ids TEXT NOT NULL DEFAULT '[]',
                    time_window_start REAL DEFAULT 0,
                    time_window_end REAL DEFAULT 0,
                    reflection_type TEXT DEFAULT 'interaction_pattern',
                    observation TEXT DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT '[]',
                    confidence REAL DEFAULT 0.5,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at REAL DEFAULT 0,
                    updated_at REAL DEFAULT 0
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rfl_conf ON reflections(conf_uid, created_at)"
            )
            # Phase 6: reusable lessons distilled from reflections
            # (additive — existing .db files gain the table on next open)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lessons (
                    lesson_id TEXT PRIMARY KEY,
                    conf_uid TEXT NOT NULL,
                    source_reflection_ids TEXT NOT NULL DEFAULT '[]',
                    lesson TEXT DEFAULT '',
                    confidence REAL DEFAULT 0.5,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at REAL DEFAULT 0,
                    updated_at REAL DEFAULT 0
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lsn_conf ON lessons(conf_uid, created_at)"
            )
            # Phase 7: condition->recommendation guidelines from lessons
            # (additive — existing .db files gain the table on next open)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategies (
                    strategy_id TEXT PRIMARY KEY,
                    conf_uid TEXT NOT NULL,
                    source_lesson_ids TEXT NOT NULL DEFAULT '[]',
                    condition TEXT DEFAULT '',
                    recommendation TEXT DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT '[]',
                    confidence REAL DEFAULT 0.5,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at REAL DEFAULT 0,
                    updated_at REAL DEFAULT 0
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_str_conf ON strategies(conf_uid, created_at)"
            )
            # Phase 8: strategy applicability judgments
            # (additive — existing .db files gain the table on next open)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    conf_uid TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    applicable INTEGER DEFAULT 0,
                    relevance REAL DEFAULT 0,
                    confidence REAL DEFAULT 0,
                    condition_match REAL DEFAULT 0,
                    reason TEXT DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT '[]',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at REAL DEFAULT 0,
                    updated_at REAL DEFAULT 0
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_evl_conf ON evaluations(conf_uid, created_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_evl_strategy ON evaluations(strategy_id)"
            )
            # Phase 9: structured decisions over evaluations
            # (additive — existing .db files gain the table on next open)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    conf_uid TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'abstain',
                    selected_strategy_id TEXT DEFAULT '',
                    selected_evaluation_id TEXT DEFAULT '',
                    confidence REAL DEFAULT 0,
                    reason TEXT DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT '[]',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at REAL DEFAULT 0,
                    updated_at REAL DEFAULT 0
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dec_conf ON decisions(conf_uid, created_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dec_eval ON decisions(selected_evaluation_id)"
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

    # -- experiences aggregate (Phase 4) -----------------------------------------

    def save_experience(self, record) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO experiences
                (experience_id, conf_uid, history_uid, interaction_type,
                 user_input, ai_response, tool_calls, outcome, outcome_type,
                 metadata, started_at, finalized_at, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.experience_id,
                    record.conf_uid,
                    record.history_uid,
                    record.interaction_type,
                    record.user_input,
                    record.ai_response,
                    json.dumps(record.tool_calls, ensure_ascii=False),
                    record.outcome,
                    record.outcome_type,
                    json.dumps(record.metadata, ensure_ascii=False),
                    record.started_at,
                    record.finalized_at,
                    record.created_at,
                    record.updated_at,
                ),
            )

    def get_experience(self, experience_id: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM experiences WHERE experience_id=?", (experience_id,)
            ).fetchone()
        return _experience_from_row(row) if row else None

    def list_experiences(self, limit: int = 200) -> List:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM experiences WHERE conf_uid=? "
                "ORDER BY finalized_at DESC, started_at DESC LIMIT ?",
                (self.conf_uid, limit),
            ).fetchall()
        return [_experience_from_row(r) for r in rows]

    def delete_experience(self, experience_id: str) -> bool:
        with self._lock, self._conn:
            affected = self._conn.execute(
                "DELETE FROM experiences WHERE experience_id=? AND conf_uid=?",
                (experience_id, self.conf_uid),
            ).rowcount
        return affected > 0

    def count_experiences(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM experiences WHERE conf_uid=?",
                (self.conf_uid,),
            ).fetchone()
        return int(row["cnt"]) if row else 0

    # -- reflections aggregate (Phase 5) ------------------------------------------

    def save_reflection(self, record) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO reflections
                (reflection_id, conf_uid, source_experience_ids,
                 time_window_start, time_window_end, reflection_type,
                 observation, evidence, confidence, metadata,
                 created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.reflection_id,
                    record.conf_uid,
                    json.dumps(record.source_experience_ids, ensure_ascii=False),
                    record.time_window_start,
                    record.time_window_end,
                    record.reflection_type,
                    record.observation,
                    json.dumps(record.evidence, ensure_ascii=False),
                    record.confidence,
                    json.dumps(record.metadata, ensure_ascii=False),
                    record.created_at,
                    record.updated_at,
                ),
            )

    def get_reflection(self, reflection_id: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM reflections WHERE reflection_id=?", (reflection_id,)
            ).fetchone()
        return _reflection_from_row(row) if row else None

    def list_reflections(self, limit: int = 200) -> List:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM reflections WHERE conf_uid=? "
                "ORDER BY created_at DESC LIMIT ?",
                (self.conf_uid, limit),
            ).fetchall()
        return [_reflection_from_row(r) for r in rows]

    def delete_reflection(self, reflection_id: str) -> bool:
        with self._lock, self._conn:
            affected = self._conn.execute(
                "DELETE FROM reflections WHERE reflection_id=? AND conf_uid=?",
                (reflection_id, self.conf_uid),
            ).rowcount
        return affected > 0

    def count_reflections(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM reflections WHERE conf_uid=?",
                (self.conf_uid,),
            ).fetchone()
        return int(row["cnt"]) if row else 0

    # -- lessons aggregate (Phase 6) ----------------------------------------------

    def save_lesson(self, record) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO lessons
                (lesson_id, conf_uid, source_reflection_ids, lesson,
                 confidence, metadata, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    record.lesson_id,
                    record.conf_uid,
                    json.dumps(record.source_reflection_ids, ensure_ascii=False),
                    record.lesson,
                    record.confidence,
                    json.dumps(record.metadata, ensure_ascii=False),
                    record.created_at,
                    record.updated_at,
                ),
            )

    def get_lesson(self, lesson_id: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM lessons WHERE lesson_id=?", (lesson_id,)
            ).fetchone()
        return _lesson_from_row(row) if row else None

    def list_lessons(self, limit: int = 200) -> List:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM lessons WHERE conf_uid=? "
                "ORDER BY created_at DESC LIMIT ?",
                (self.conf_uid, limit),
            ).fetchall()
        return [_lesson_from_row(r) for r in rows]

    def list_lessons_by_reflection(self, reflection_id: str) -> List:
        rows = self.list_lessons(limit=10000)
        return [r for r in rows if reflection_id in r.source_reflection_ids]

    def delete_lesson(self, lesson_id: str) -> bool:
        with self._lock, self._conn:
            affected = self._conn.execute(
                "DELETE FROM lessons WHERE lesson_id=? AND conf_uid=?",
                (lesson_id, self.conf_uid),
            ).rowcount
        return affected > 0

    def count_lessons(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM lessons WHERE conf_uid=?",
                (self.conf_uid,),
            ).fetchone()
        return int(row["cnt"]) if row else 0

    # -- strategies aggregate (Phase 7) ---------------------------------------------

    def save_strategy(self, record) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO strategies
                (strategy_id, conf_uid, source_lesson_ids, condition,
                 recommendation, evidence, confidence, metadata,
                 created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.strategy_id,
                    record.conf_uid,
                    json.dumps(record.source_lesson_ids, ensure_ascii=False),
                    record.condition,
                    record.recommendation,
                    json.dumps(record.evidence, ensure_ascii=False),
                    record.confidence,
                    json.dumps(record.metadata, ensure_ascii=False),
                    record.created_at,
                    record.updated_at,
                ),
            )

    def get_strategy(self, strategy_id: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM strategies WHERE strategy_id=?", (strategy_id,)
            ).fetchone()
        return _strategy_from_row(row) if row else None

    def list_strategies(self, limit: int = 200) -> List:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM strategies WHERE conf_uid=? "
                "ORDER BY created_at DESC LIMIT ?",
                (self.conf_uid, limit),
            ).fetchall()
        return [_strategy_from_row(r) for r in rows]

    def list_strategies_by_lesson(self, lesson_id: str) -> List:
        rows = self.list_strategies(limit=10000)
        return [r for r in rows if lesson_id in r.source_lesson_ids]

    def delete_strategy(self, strategy_id: str) -> bool:
        with self._lock, self._conn:
            affected = self._conn.execute(
                "DELETE FROM strategies WHERE strategy_id=? AND conf_uid=?",
                (strategy_id, self.conf_uid),
            ).rowcount
        return affected > 0

    def count_strategies(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM strategies WHERE conf_uid=?",
                (self.conf_uid,),
            ).fetchone()
        return int(row["cnt"]) if row else 0

    # -- evaluations aggregate (Phase 8) ---------------------------------------------

    def save_evaluation(self, record) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO evaluations
                (evaluation_id, conf_uid, strategy_id, applicable,
                 relevance, confidence, condition_match, reason,
                 evidence, metadata, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.evaluation_id,
                    record.conf_uid,
                    record.strategy_id,
                    1 if record.applicable else 0,
                    record.relevance,
                    record.confidence,
                    record.condition_match,
                    record.reason,
                    json.dumps(record.evidence, ensure_ascii=False),
                    json.dumps(record.metadata, ensure_ascii=False),
                    record.created_at,
                    record.updated_at,
                ),
            )

    def get_evaluation(self, evaluation_id: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM evaluations WHERE evaluation_id=?", (evaluation_id,)
            ).fetchone()
        return _evaluation_from_row(row) if row else None

    def list_evaluations(self, limit: int = 200) -> List:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM evaluations WHERE conf_uid=? "
                "ORDER BY created_at DESC LIMIT ?",
                (self.conf_uid, limit),
            ).fetchall()
        return [_evaluation_from_row(r) for r in rows]

    def list_evaluations_by_strategy(self, strategy_id: str) -> List:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM evaluations WHERE conf_uid=? AND strategy_id=? "
                "ORDER BY created_at DESC",
                (self.conf_uid, strategy_id),
            ).fetchall()
        return [_evaluation_from_row(r) for r in rows]

    def delete_evaluation(self, evaluation_id: str) -> bool:
        with self._lock, self._conn:
            affected = self._conn.execute(
                "DELETE FROM evaluations WHERE evaluation_id=? AND conf_uid=?",
                (evaluation_id, self.conf_uid),
            ).rowcount
        return affected > 0

    def count_evaluations(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM evaluations WHERE conf_uid=?",
                (self.conf_uid,),
            ).fetchone()
        return int(row["cnt"]) if row else 0

    # -- decisions aggregate (Phase 9) ----------------------------------------------

    def save_decision(self, record) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO decisions
                (decision_id, conf_uid, status, selected_strategy_id,
                 selected_evaluation_id, confidence, reason, evidence,
                 metadata, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.decision_id,
                    record.conf_uid,
                    record.status,
                    record.selected_strategy_id,
                    record.selected_evaluation_id,
                    record.confidence,
                    record.reason,
                    json.dumps(record.evidence, ensure_ascii=False),
                    json.dumps(record.metadata, ensure_ascii=False),
                    record.created_at,
                    record.updated_at,
                ),
            )

    def get_decision(self, decision_id: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM decisions WHERE decision_id=?", (decision_id,)
            ).fetchone()
        return _decision_from_row(row) if row else None

    def list_decisions(self, limit: int = 200) -> List:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM decisions WHERE conf_uid=? "
                "ORDER BY created_at DESC LIMIT ?",
                (self.conf_uid, limit),
            ).fetchall()
        return [_decision_from_row(r) for r in rows]

    def list_decisions_by_strategy(self, strategy_id: str) -> List:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM decisions WHERE conf_uid=? AND selected_strategy_id=? "
                "ORDER BY created_at DESC",
                (self.conf_uid, strategy_id),
            ).fetchall()
        return [_decision_from_row(r) for r in rows]

    def list_decisions_by_evaluation(self, evaluation_id: str) -> List:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM decisions WHERE conf_uid=? AND selected_evaluation_id=? "
                "ORDER BY created_at DESC",
                (self.conf_uid, evaluation_id),
            ).fetchall()
        return [_decision_from_row(r) for r in rows]

    def delete_decision(self, decision_id: str) -> bool:
        with self._lock, self._conn:
            affected = self._conn.execute(
                "DELETE FROM decisions WHERE decision_id=? AND conf_uid=?",
                (decision_id, self.conf_uid),
            ).rowcount
        return affected > 0

    def count_decisions(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM decisions WHERE conf_uid=?",
                (self.conf_uid,),
            ).fetchone()
        return int(row["cnt"]) if row else 0

    # -- lifecycle -------------------------------------------------------------

    def close(self) -> None:
        try:
            with self._lock:
                self._conn.close()
        except Exception as e:
            logger.warning(f"[LTM] store close error: {e}")


def _experience_from_row(row: sqlite3.Row):
    """sqlite Row -> ExperienceRecord (lazy import: the experience domain
    imports this package's Protocol, so a module-level import would cycle)."""
    from ...experience.schemas import ExperienceRecord  # noqa: PLC0415

    return ExperienceRecord(
        experience_id=row["experience_id"],
        conf_uid=row["conf_uid"],
        history_uid=row["history_uid"] or "",
        interaction_type=row["interaction_type"] or "chat",
        user_input=row["user_input"] or "",
        ai_response=row["ai_response"] or "",
        tool_calls=json.loads(row["tool_calls"] or "[]"),
        outcome=row["outcome"] or "",
        outcome_type=row["outcome_type"] or "turn_complete",
        metadata=json.loads(row["metadata"] or "{}"),
        started_at=float(row["started_at"] or 0.0),
        finalized_at=float(row["finalized_at"] or 0.0),
        created_at=float(row["created_at"] or 0.0),
        updated_at=float(row["updated_at"] or 0.0),
    )


def _reflection_from_row(row: sqlite3.Row):
    """sqlite Row -> ReflectionRecord (lazy import, same cycle reason)."""
    from ...reflection.schemas import ReflectionRecord  # noqa: PLC0415

    return ReflectionRecord(
        reflection_id=row["reflection_id"],
        conf_uid=row["conf_uid"],
        source_experience_ids=json.loads(row["source_experience_ids"] or "[]"),
        time_window_start=float(row["time_window_start"] or 0.0),
        time_window_end=float(row["time_window_end"] or 0.0),
        reflection_type=row["reflection_type"] or "interaction_pattern",
        observation=row["observation"] or "",
        evidence=json.loads(row["evidence"] or "[]"),
        confidence=float(row["confidence"] or 0.5),
        metadata=json.loads(row["metadata"] or "{}"),
        created_at=float(row["created_at"] or 0.0),
        updated_at=float(row["updated_at"] or 0.0),
    )


def _lesson_from_row(row: sqlite3.Row):
    """sqlite Row -> LessonRecord (lazy import, same cycle reason)."""
    from ...lesson.schemas import LessonRecord  # noqa: PLC0415

    return LessonRecord(
        lesson_id=row["lesson_id"],
        conf_uid=row["conf_uid"],
        source_reflection_ids=json.loads(row["source_reflection_ids"] or "[]"),
        lesson=row["lesson"] or "",
        confidence=float(row["confidence"] or 0.5),
        metadata=json.loads(row["metadata"] or "{}"),
        created_at=float(row["created_at"] or 0.0),
        updated_at=float(row["updated_at"] or 0.0),
    )


def _strategy_from_row(row: sqlite3.Row):
    """sqlite Row -> StrategyRecord (lazy import, same cycle reason)."""
    from ...strategy.schemas import StrategyRecord  # noqa: PLC0415

    return StrategyRecord(
        strategy_id=row["strategy_id"],
        conf_uid=row["conf_uid"],
        source_lesson_ids=json.loads(row["source_lesson_ids"] or "[]"),
        condition=row["condition"] or "",
        recommendation=row["recommendation"] or "",
        evidence=json.loads(row["evidence"] or "[]"),
        confidence=float(row["confidence"] or 0.5),
        metadata=json.loads(row["metadata"] or "{}"),
        created_at=float(row["created_at"] or 0.0),
        updated_at=float(row["updated_at"] or 0.0),
    )


def _evaluation_from_row(row: sqlite3.Row):
    """sqlite Row -> EvaluationRecord (lazy import, same cycle reason)."""
    from ...evaluation.schemas import EvaluationRecord  # noqa: PLC0415

    return EvaluationRecord(
        evaluation_id=row["evaluation_id"],
        conf_uid=row["conf_uid"],
        strategy_id=row["strategy_id"] or "",
        applicable=bool(row["applicable"]),
        relevance=float(row["relevance"] or 0.0),
        confidence=float(row["confidence"] or 0.0),
        condition_match=float(row["condition_match"] or 0.0),
        reason=row["reason"] or "",
        evidence=json.loads(row["evidence"] or "[]"),
        metadata=json.loads(row["metadata"] or "{}"),
        created_at=float(row["created_at"] or 0.0),
        updated_at=float(row["updated_at"] or 0.0),
    )


def _decision_from_row(row: sqlite3.Row):
    """sqlite Row -> DecisionRecord (lazy import, same cycle reason)."""
    from ...decision.schemas import DecisionRecord  # noqa: PLC0415

    return DecisionRecord(
        decision_id=row["decision_id"],
        conf_uid=row["conf_uid"],
        status=row["status"] or "abstain",
        selected_strategy_id=row["selected_strategy_id"] or "",
        selected_evaluation_id=row["selected_evaluation_id"] or "",
        confidence=float(row["confidence"] or 0.0),
        reason=row["reason"] or "",
        evidence=json.loads(row["evidence"] or "[]"),
        metadata=json.loads(row["metadata"] or "{}"),
        created_at=float(row["created_at"] or 0.0),
        updated_at=float(row["updated_at"] or 0.0),
    )
