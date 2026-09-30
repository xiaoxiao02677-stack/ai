"""Storage subpackage: provider contract + SQLite provider + repositories.

Layering (Phase 1B):

    MemoryStore (compat facade, ../store.py)
        ├─ MemoryRepository    — memories table
        ├─ StateRepository     — user_state table
        ├─ SummaryRepository   — summary table
        ├─ KeywordRepository   — keywords table
        └─ all sharing one StorageProvider

    StorageProvider            — the contract (provider.py, typing.Protocol)
        └─ SQLiteStorageProvider   — the SQLite implementation

Repositories depend only on the ``StorageProvider`` Protocol primitives
(``execute`` / ``query_one`` / ``query_rows`` / ``transaction`` /
``row_to_memory_record``) — never on a concrete class, a lock or a
connection. A future Hermes provider implements the same Protocol and can
be swapped in without touching any repository.
"""

from .provider import StorageProvider, StorageTransaction
from .sqlite_provider import SQLiteStorageProvider
from .memory_repository import MemoryRepository
from .state_repository import StateRepository
from .summary_repository import SummaryRepository
from .keyword_repository import KeywordRepository

__all__ = [
    "StorageProvider",
    "StorageTransaction",
    "SQLiteStorageProvider",
    "MemoryRepository",
    "StateRepository",
    "SummaryRepository",
    "KeywordRepository",
]
