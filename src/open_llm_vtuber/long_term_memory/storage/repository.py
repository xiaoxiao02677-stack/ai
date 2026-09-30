"""Domain repositories: business rules over the StorageProvider aggregates.

Phase-2 refactor: Phase-1B's four *table-shaped* repositories
(memory_repository / state_repository / summary_repository /
keyword_repository, each a thin SQL proxy) are consolidated here into
four *domain-shaped* repositories. All SQL has sunk into the provider
(``sqlite_provider.py``); what remains in this module is domain logic
only:

  * ``MemoryRepository``  — the ``updated_at`` stamp on update, and the
    term-hit scoring of ``search_active`` (pure in-memory ranking over
    ``MemoryRecord`` fields);
  * ``KeywordRepository`` — input normalization (strip + drop empty);
  * ``StateRepository``   — the ``updated_at`` stamp on save;
  * ``SummaryRepository`` — nothing beyond delegation (kept for a stable
    composition surface).

Repositories are typed against the aggregate ``StorageProvider``
Protocol and never see a SQL string, a cursor, a lock or a connection.
"""

import time
from typing import List, Optional

from ..schemas import KeywordRecord, MemoryRecord, UserState
from .provider import StorageProvider


class MemoryRepository:
    """Domain access to the user's long-term memories (one conf_uid scope)."""

    def __init__(self, provider: StorageProvider):
        self.provider = provider

    @property
    def conf_uid(self) -> str:
        return self.provider.conf_uid

    @property
    def db_path(self) -> str:
        return self.provider.db_path

    def add_memory(self, record: MemoryRecord) -> None:
        self.provider.save_memory(record)

    def update_memory(self, record: MemoryRecord) -> None:
        record.updated_at = time.time()
        self.provider.save_memory(record)

    def get_memory(self, memory_id: str) -> Optional[MemoryRecord]:
        return self.provider.get_memory(memory_id)

    def list_memories(
        self, status: str = "active", memory_type: Optional[str] = None
    ) -> List[MemoryRecord]:
        return self.provider.list_memories(status=status, memory_type=memory_type)

    def list_all_memories(self) -> List[MemoryRecord]:
        return self.provider.list_all_memories()

    def delete_memory(self, memory_id: str) -> bool:
        return self.provider.delete_memory(memory_id)

    def find_active_by_content(self, content: str) -> Optional[MemoryRecord]:
        return self.provider.find_active_by_content(content)

    def search_active(
        self, terms: List[str], limit: int = 40
    ) -> List[MemoryRecord]:
        """Term-match search over active memories (phase-1 retrieval core).

        A memory matches when any term appears in its content or keywords.
        Ranking is in-memory (hit count) — a provider is free to push this
        down, but the semantics stay identical.
        """
        if not terms:
            return []
        scored = []
        for rec in self.provider.list_memories(status="active"):
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
        self.provider.mark_used(memory_ids)

    def count_memories(self, status: str = "active") -> int:
        return self.provider.count_memories(status=status)


class KeywordRepository:
    """Domain access to extracted keywords (one conf_uid scope)."""

    def __init__(self, provider: StorageProvider):
        self.provider = provider

    @property
    def conf_uid(self) -> str:
        return self.provider.conf_uid

    def upsert_keyword(self, keyword: str, category: str) -> None:
        kw = keyword.strip()
        if not kw:
            return
        self.provider.upsert_keyword(kw, category)

    def list_keywords(self, limit: int = 200) -> List[KeywordRecord]:
        return self.provider.list_keywords(limit=limit)

    def delete_keyword(self, keyword: str, category: str) -> bool:
        return self.provider.delete_keyword(keyword, category)

    def count_keywords(self) -> int:
        return self.provider.count_keywords()


class StateRepository:
    """Domain access to the rolling user state (one conf_uid scope)."""

    def __init__(self, provider: StorageProvider):
        self.provider = provider

    def get_state(self) -> Optional[UserState]:
        return self.provider.get_state()

    def save_state(self, state: UserState) -> None:
        state.updated_at = time.time()
        self.provider.save_state(state)


class SummaryRepository:
    """Domain access to the conversation summary + turn count."""

    def __init__(self, provider: StorageProvider):
        self.provider = provider

    def get_summary(self) -> str:
        return self.provider.get_summary()

    def save_summary(self, text: str, turn_count: int) -> None:
        self.provider.save_summary(text, turn_count)

    def get_turn_count(self) -> int:
        return self.provider.get_turn_count()

    def bump_turn_count(self) -> int:
        return self.provider.bump_turn_count()
