"""MemoryStore: backward-compatible facade over the storage subpackage.

Phase-1A refactor: the original 406-line multi-responsibility file was
split into long_term_memory/storage/ (SQLiteStorageProvider +
MemoryRepository / StateRepository / SummaryRepository / KeywordRepository).
This class keeps the exact public API that the rest of the project uses
(manager.py, retriever.py, memory_panel.py, run_tests.py):

    MemoryStore(conf_uid) — same constructor
    all 25 public methods — same signatures, same behavior
    .conf_uid / .db_path / .close() — same attributes

Every method delegates 1:1 to the matching repository. Behavior is
byte-equivalent: SQL, locking, time side-effects (update_memory stamps
updated_at via the repository, exactly as before).

Phase-1B: the facade no longer contains any SQL. ``stats()`` used to reach
through the provider's private lock/connection; it now composes repository
counts. Both the facade and the repositories depend on the StorageProvider
Protocol, not on the SQLite class.

To query memories in new code, prefer MemoryRepository directly; keep
using MemoryStore where the legacy surface is expected.
"""

from typing import Any, Dict, List, Optional

from .schemas import MemoryRecord, KeywordRecord, UserState
from .storage import (
    SQLiteStorageProvider,
    MemoryRepository,
    StateRepository,
    SummaryRepository,
    KeywordRepository,
)


class MemoryStore:
    """SQLite-backed store for memories, keywords and user state.

    Compat facade: wiring + delegation only. See storage/ subpackage for
    the actual implementations.
    """

    def __init__(self, conf_uid: str):
        self.provider = SQLiteStorageProvider(conf_uid)
        self.memories = MemoryRepository(self.provider)
        self.state_repo = StateRepository(self.provider)
        self.summary_repo = SummaryRepository(self.provider)
        self.keywords = KeywordRepository(self.provider)

    # -- forwarded attributes ---------------------------------------------------

    @property
    def conf_uid(self) -> str:
        return self.provider.conf_uid

    @property
    def db_path(self) -> str:
        return self.provider.db_path

    # -- memories ---------------------------------------------------------------

    def add_memory(self, record: MemoryRecord) -> None:
        self.memories.add_memory(record)

    def update_memory(self, record: MemoryRecord) -> None:
        self.memories.update_memory(record)

    def get_memory(self, memory_id: str) -> Optional[MemoryRecord]:
        return self.memories.get_memory(memory_id)

    def list_memories(
        self, status: str = "active", memory_type: Optional[str] = None
    ) -> List[MemoryRecord]:
        return self.memories.list_memories(status=status, memory_type=memory_type)

    def list_all_memories(self) -> List[MemoryRecord]:
        return self.memories.list_all_memories()

    def delete_memory(self, memory_id: str) -> bool:
        return self.memories.delete_memory(memory_id)

    def find_active_by_content(self, content: str) -> Optional[MemoryRecord]:
        return self.memories.find_active_by_content(content)

    def search_active(
        self, terms: List[str], limit: int = 40
    ) -> List[MemoryRecord]:
        return self.memories.search_active(terms, limit=limit)

    def mark_used(self, memory_ids: List[str]) -> None:
        self.memories.mark_used(memory_ids)

    def count_memories(self, status: str = "active") -> int:
        return self.memories.count_memories(status=status)

    # -- keywords ---------------------------------------------------------------

    def upsert_keyword(self, keyword: str, category: str) -> None:
        self.keywords.upsert_keyword(keyword, category)

    def list_keywords(self, limit: int = 200) -> List[KeywordRecord]:
        return self.keywords.list_keywords(limit=limit)

    def delete_keyword(self, keyword: str, category: str) -> bool:
        return self.keywords.delete_keyword(keyword, category)

    # -- user state -------------------------------------------------------------

    def get_state(self) -> Optional[UserState]:
        return self.state_repo.get_state()

    def save_state(self, state: UserState) -> None:
        self.state_repo.save_state(state)

    # -- short-term summary -----------------------------------------------------

    def get_summary(self) -> str:
        return self.summary_repo.get_summary()

    def save_summary(self, text: str, turn_count: int) -> None:
        self.summary_repo.save_summary(text, turn_count)

    def get_turn_count(self) -> int:
        return self.summary_repo.get_turn_count()

    def bump_turn_count(self) -> int:
        return self.summary_repo.bump_turn_count()

    # -- maintenance ------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        return {
            "active_memories": self.memories.count_memories("active"),
            "deprecated_memories": self.memories.count_memories("deprecated"),
            "keywords": self.keywords.count_keywords(),
            "turn_count": self.get_turn_count(),
            "db_path": self.db_path,
        }

    def close(self) -> None:
        self.provider.close()
