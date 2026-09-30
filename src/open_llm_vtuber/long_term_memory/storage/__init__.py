"""Storage subpackage: provider contract + SQLite provider + repositories.

Layering (Phase 2):

    MemoryManager / MemoryRetriever  (business layer, zero SQL)
        ├─ MemoryRepository   — domain rules for memories
        ├─ KeywordRepository  — input normalization for keywords
        ├─ StateRepository    — updated_at stamp for user state
        ├─ SummaryRepository  — delegation surface for summary/turns
        └─ all sharing one StorageProvider

    StorageProvider            — the contract (provider.py, aggregate-shaped
                                 Protocol: domain objects in, domain objects out)
        └─ SQLiteStorageProvider   — the SQLite implementation, and the
                                     ONLY file in the system containing SQL

Repositories depend only on the ``StorageProvider`` Protocol — never on a
concrete class, a SQL string, a cursor, a lock or a connection. A future
Hermes provider implements the same Protocol and can be swapped in at the
composition root (``store.py``) without touching any repository.
"""

from .provider import StorageProvider
from .sqlite_provider import SQLiteStorageProvider
from .repository import (
    MemoryRepository,
    KeywordRepository,
    StateRepository,
    SummaryRepository,
)

__all__ = [
    "StorageProvider",
    "SQLiteStorageProvider",
    "MemoryRepository",
    "KeywordRepository",
    "StateRepository",
    "SummaryRepository",
]
