"""Memory domain repository: CRUD + search over the memories table.

Phase-1A refactor: extracted verbatim from store.py. Phase-1B: now typed
against the ``StorageProvider`` Protocol and expressed entirely through
provider primitives (``execute`` / ``query_one`` / ``query_rows`` /
``transaction``) — it no longer imports or names the SQLite class, and
never touches a lock or connection.

What stays here is domain logic, not storage plumbing:
  * the hits-scoring in ``search_active`` (pure in-memory ranking over
    ``MemoryRecord`` fields);
  * the ``updated_at`` stamp in ``update_memory``.

``search_active`` still composes over ``list_memories``.
"""

import json
import time
from typing import List, Optional

from ..schemas import MemoryRecord
from .provider import StorageProvider


class MemoryRepository:
    """Data access for the memories table (one conf_uid scope)."""

    def __init__(self, provider: StorageProvider):
        self.provider = provider

    # -- properties forwarded for compatibility -------------------------------

    @property
    def conf_uid(self) -> str:
        return self.provider.conf_uid

    @property
    def db_path(self) -> str:
        return self.provider.db_path

    # -- memories -------------------------------------------------------------

    def add_memory(self, record: MemoryRecord) -> None:
        self.provider.execute(
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

    def update_memory(self, record: MemoryRecord) -> None:
        record.updated_at = time.time()
        self.add_memory(record)

    def get_memory(self, memory_id: str) -> Optional[MemoryRecord]:
        row = self.provider.query_one(
            "SELECT * FROM memories WHERE memory_id=?", (memory_id,)
        )
        return self.provider.row_to_memory_record(row) if row else None

    def list_memories(
        self, status: str = "active", memory_type: Optional[str] = None
    ) -> List[MemoryRecord]:
        q = "SELECT * FROM memories WHERE conf_uid=? AND status=?"
        params: List = [self.provider.conf_uid, status]
        if memory_type:
            q += " AND memory_type=?"
            params.append(memory_type)
        q += " ORDER BY importance DESC, updated_at DESC"
        rows = self.provider.query_rows(q, params)
        return [self.provider.row_to_memory_record(r) for r in rows]

    def list_all_memories(self) -> List[MemoryRecord]:
        rows = self.provider.query_rows(
            "SELECT * FROM memories WHERE conf_uid=? ORDER BY status, importance DESC, updated_at DESC",
            (self.provider.conf_uid,),
        )
        return [self.provider.row_to_memory_record(r) for r in rows]

    def delete_memory(self, memory_id: str) -> bool:
        affected = self.provider.execute(
            "DELETE FROM memories WHERE memory_id=? AND conf_uid=?",
            (memory_id, self.provider.conf_uid),
        )
        return affected > 0

    def find_active_by_content(self, content: str) -> Optional[MemoryRecord]:
        """Exact-content lookup used by the deduplicator."""
        row = self.provider.query_one(
            "SELECT * FROM memories WHERE conf_uid=? AND content=? AND status='active'",
            (self.provider.conf_uid, content),
        )
        return self.provider.row_to_memory_record(row) if row else None

    def search_active(
        self, terms: List[str], limit: int = 40
    ) -> List[MemoryRecord]:
        """Keyword LIKE search over active memories (phase-1 retrieval core).

        A memory matches when any term appears in its content or keywords.
        """
        if not terms:
            return []
        records = self.list_memories(status="active")
        scored = []
        for rec in records:
            blob = rec.content.lower()
            kw_blob = " ".join(k.lower() for k in rec.keywords)
            hits = 0
            for t in terms:
                tl = t.lower()
                if tl and (tl in blob or tl in kw_blob):
                    hits += 1
            if hits > 0:
                scored.append((hits, rec))
        scored.sort(key=lambda x: -x[0])
        return [rec for _, rec in scored[:limit]]

    def mark_used(self, memory_ids: List[str]) -> None:
        """Bump use_count / last_used_at for injected memories."""
        now = time.time()
        with self.provider.transaction() as tx:
            for mid in memory_ids:
                tx.execute(
                    "UPDATE memories SET use_count=use_count+1, last_used_at=? "
                    "WHERE memory_id=?",
                    (now, mid),
                )

    def count_memories(self, status: str = "active") -> int:
        row = self.provider.query_one(
            "SELECT COUNT(*) AS cnt FROM memories WHERE conf_uid=? AND status=?",
            (self.provider.conf_uid, status),
        )
        return int(row["cnt"]) if row else 0
