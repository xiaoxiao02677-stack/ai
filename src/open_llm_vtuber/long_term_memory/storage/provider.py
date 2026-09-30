"""StorageProvider contract: the storage primitive surface repositories need.

Phase-1B refactor: Phase-1A left the contract *implicit* — repositories
reached into the concrete ``SQLiteStorageProvider`` via private attributes
(``_lock`` / ``_conn``) and the concrete class name in type hints. That
made ``SQLiteStorageProvider`` impossible to swap.

This module promotes the implicit contract to an explicit ``typing.Protocol``
so that:

    MemoryManager / MemoryStore / repositories
        depend on  ->  StorageProvider (this Protocol, structural)

    SQLiteStorageProvider (concrete, owns all SQL + sqlite3 objects)
        implements -> StorageProvider

    a future HermesProvider
        implements -> StorageProvider   (no repository change required)

Design rules (deliberately minimal — no repository base classes, no
generic CRUD, no storage factory):

* The provider owns **everything database-specific**: connection,
  schema/DDL, the thread lock, transaction boundaries, sqlite3 objects.
* Repositories own **domain rules only** (which columns to write, input
  normalization, timestamp stamping, in-memory scoring) and express all
  persistence through the primitives below.
* No primitive leaks a sqlite3 object. Repositories never see a
  connection, a cursor or a lock.
* Read primitives do not open a transaction; write primitives commit;
  multi-statement write atomicity is expressed with ``transaction()``.

Row shape: ``query_one`` / ``query_rows`` return provider-native rows that
are only ever consumed by ``row_to_memory_record`` (or by index/key in the
few repositories that read a single scalar column). Providers therefore
remain free to choose their own row representation.
"""

from contextlib import AbstractContextManager
from typing import Any, Optional, Protocol, Sequence, runtime_checkable


@runtime_checkable
class StorageTransaction(Protocol):
    """Handle for a single atomic write scope opened by ``transaction()``.

    Statements executed on this handle commit together on clean exit and
    roll back together if an exception escapes.
    """

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a write statement, returning the affected row count."""
        ...

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Any]:
        """Read a single row inside the transaction (or ``None``)."""
        ...

    def query_rows(self, sql: str, params: Sequence[Any] = ()) -> Sequence[Any]:
        """Read all rows inside the transaction."""
        ...


@runtime_checkable
class StorageProvider(Protocol):
    """Structural contract every storage backend must satisfy.

    Implementations: ``SQLiteStorageProvider`` today, ``HermesProvider``
    in a future phase. Repositories are typed against this Protocol, so
    switching backends requires **no repository change**.
    """

    # -- identity -------------------------------------------------------------

    conf_uid: str
    """Scope key this provider instance is bound to (one store per conf_uid)."""

    db_path: str
    """Location/handle of the backing store (informational; may be a URI for
    non-file backends)."""

    # -- write primitives -----------------------------------------------------

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a single write statement in its own transaction.

        Returns the number of affected rows. Used for one-shot INSERT /
        UPDATE / DELETE where only the row count matters.
        """
        ...

    # -- read primitives ------------------------------------------------------

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Any]:
        """Return the first matching row, or ``None``."""
        ...

    def query_rows(self, sql: str, params: Sequence[Any] = ()) -> Sequence[Any]:
        """Return all matching rows as a sequence."""
        ...

    # -- row mapping ----------------------------------------------------------

    def row_to_memory_record(self, row: Any) -> Any:
        """Map a provider-native row to a ``MemoryRecord``.

        Declared on the provider (not the repository) because the row
        layout is a storage concern: a non-SQL provider supplies its own
        mapping.
        """
        ...

    # -- transactional scope --------------------------------------------------

    def transaction(self) -> AbstractContextManager:
        """Open an atomic write scope.

        Yields a ``StorageTransaction``. Nested or stepwise multi-statement
        writes (read-then-write UPSERT, batched UPDATE) go through this so
        atomicity and lock acquisition stay identical to the pre-refactor
        ``with provider._lock, provider._conn:`` blocks.
        """
        ...

    # -- lifecycle ------------------------------------------------------------

    def close(self) -> None:
        """Release the underlying connection/resources."""
        ...
