"""Storage subpackage: provider contract + backends + repositories.

Layering (Phase 3):

    MemoryManager / MemoryRetriever  (business layer, zero SQL, zero backend names)
        ├─ MemoryRepository   — domain rules for memories
        ├─ KeywordRepository  — input normalization for keywords
        ├─ StateRepository    — updated_at stamp for user state
        ├─ SummaryRepository  — delegation surface for summary/turns
        └─ all sharing one StorageProvider

    StorageProvider            — the contract (provider.py, aggregate-shaped
                                 Protocol: domain objects in, domain objects out)
        ├─ SQLiteStorageProvider   — default backend; the only file with SQL
        └─ HermesStorageProvider   — REST adapter over the Hermes Memory API
                                     (+ JSON sidecar for LTM-only aggregates)

``create_storage_provider`` (provider_factory.py) is the **composition
point**: it reads the backend choice from the LTM config and returns the
concrete provider. Manager / Store / Repository / Retriever never see a
backend class name. Adding another backend = one more factory branch.
"""

from .provider import StorageProvider
from .sqlite_provider import SQLiteStorageProvider
from .provider_factory import (
    create_storage_provider,
    PROVIDER_SQLITE,
    PROVIDER_HERMES,
)
from .repository import (
    MemoryRepository,
    KeywordRepository,
    StateRepository,
    SummaryRepository,
)


def create_default_provider(conf_uid: str) -> StorageProvider:
    """Backward-compatible default backend (sqlite, no config read).

    Kept for callers that predate config-driven selection; the config
    path goes through ``create_storage_provider(conf_uid, config)``.
    """
    return create_storage_provider(conf_uid, {"storage": {"provider": "sqlite"}})


__all__ = [
    "StorageProvider",
    "SQLiteStorageProvider",
    "create_default_provider",
    "create_storage_provider",
    "PROVIDER_SQLITE",
    "PROVIDER_HERMES",
    "MemoryRepository",
    "KeywordRepository",
    "StateRepository",
    "SummaryRepository",
]
