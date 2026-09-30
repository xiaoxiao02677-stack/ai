"""MemoryStore: backward-compatible facade over the storage subpackage.

Phase-1A refactor: the original 406-line multi-responsibility file was
split into long_term_memory/storage/. Phase-1B typed everything against
a SQL-shaped storage protocol. Phase-2 made the protocol aggregate-shaped
(domain objects in/out), sank every SQL statement into the storage
subpackage, and moved backend selection behind
``storage.create_default_provider`` — this facade no longer names any
concrete backend or storage technology.

This class remains the exact public API the rest of the project uses
(manager.py, memory_panel.py, run_tests.py):

    MemoryStore(conf_uid) — same constructor
    all 21 public methods — same signatures, same behavior
    .conf_uid / .db_path / .close() — same attributes

To query memories in new code, prefer the domain repositories directly
(``store.memories`` / ``store.keywords`` / ``store.state_repo`` /
``store.summary_repo``); keep using MemoryStore where the legacy surface
is expected (management panel, tests).
"""

from typing import Any, Dict, List, Optional

from .schemas import MemoryRecord, KeywordRecord, UserState
from . import storage
from .storage import (
    MemoryRepository,
    StateRepository,
    SummaryRepository,
    KeywordRepository,
)


class MemoryStore(MemoryRepository):
    """Store for memories, keywords and user state (compat facade).

    Wiring + delegation only; the storage subpackage owns the backend
    choice, all persistence semantics and every implementation detail.

    ``config`` (optional, the LTM module config) selects the backend via
    ``storage.create_storage_provider``; omitted/None keeps the default
    sqlite backend — existing callers behave exactly as before.
    """

    def __init__(self, conf_uid: str, config: "dict | None" = None):
        # backend choice lives behind the storage package's factory —
        # resolved through the module attribute so swapping the whole
        # stack (including from tests) is a one-line change there
        self.provider = storage.create_storage_provider(conf_uid, config)
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
    # (add/update/get/list/delete/find/search/mark_used/count are inherited
    #  from MemoryRepository — same signatures, repo-backed)

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
