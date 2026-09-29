"""Storage subpackage: provider + per-domain repositories.

Layering (Phase 1A):

    MemoryStore (compat facade, ../store.py)
        ├─ MemoryRepository    — memories table
        ├─ StateRepository     — user_state table
        ├─ SummaryRepository   — summary table
        ├─ KeywordRepository   — keywords table
        └─ all sharing one SQLiteStorageProvider (connection/lock/schema)

A future Hermes provider would replace SQLiteStorageProvider behind the
same repository surface; repositories must only rely on the provider
attributes used here (conf_uid, db_path, _lock, _conn, row mapping).
"""

from .sqlite_provider import SQLiteStorageProvider
from .memory_repository import MemoryRepository
from .state_repository import StateRepository
from .summary_repository import SummaryRepository
from .keyword_repository import KeywordRepository

__all__ = [
    "SQLiteStorageProvider",
    "MemoryRepository",
    "StateRepository",
    "SummaryRepository",
    "KeywordRepository",
]
