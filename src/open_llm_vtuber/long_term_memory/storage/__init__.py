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
        └─ SQLiteStorageProvider   — the default implementation, and the
                                     ONLY file in the system containing SQL

Repositories depend only on the ``StorageProvider`` Protocol — never on a
concrete class, a SQL string, a cursor, a lock or a connection.

``create_default_provider`` below is the **composition point** of the whole
persistence stack: every store above this package obtains its provider
through it. Swapping the backend (e.g. a future HermesProvider) is a
one-line change in this factory and touches nothing else in the project.
"""

from .provider import StorageProvider
from .sqlite_provider import SQLiteStorageProvider
from .repository import (
    MemoryRepository,
    KeywordRepository,
    StateRepository,
    SummaryRepository,
)


def create_default_provider(conf_uid: str) -> StorageProvider:
    """Build the storage backend this build ships with (one per conf_uid).

    The single place a concrete provider is chosen. ``MemoryStore`` and
    the repositories never name a concrete backend; pointing this factory
    at another ``StorageProvider`` implementation swaps the whole stack.
    """
    return SQLiteStorageProvider(conf_uid)


__all__ = [
    "StorageProvider",
    "SQLiteStorageProvider",
    "create_default_provider",
    "MemoryRepository",
    "KeywordRepository",
    "StateRepository",
    "SummaryRepository",
]
