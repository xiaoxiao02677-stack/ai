"""StorageProvider contract: domain-aggregate persistence surface.

Phase-2 refactor: Phase-1B's protocol was still SQL-shaped — five
primitives (``execute`` / ``query_one`` / ``query_rows`` / ``transaction``
/ ``row_to_memory_record``) that forced every repository to compose SQL
strings. SQL never leaked above the repository line, but the *contract*
did: swapping SQLite for another backend still meant rewriting SQL in
four repositories.

This protocol is now aggregate-shaped: domain objects (``MemoryRecord``
/ ``KeywordRecord`` / ``UserState``) in, domain objects out.

    MemoryManager / MemoryRetriever
        depend on  ->  repositories (repository.py, domain rules)
    repositories
        depend on  ->  StorageProvider (this Protocol, aggregate-shaped)
    SQLiteStorageProvider (concrete, owns ALL SQL + sqlite3 objects)
        implements -> StorageProvider

    a future HermesProvider implements the same Protocol and can be
    swapped in by changing exactly one line — the composition root in
    store.py, the only place that imports a concrete provider.

Design rules (carried over from Phase 1B):

* The provider owns **everything database-specific**: connection,
  schema/DDL, the thread lock, transaction boundaries, sqlite3 objects,
  row <-> domain-object mapping, and every SQL string in the system.
* Repositories own **domain rules only** (timestamp stamping, input
  normalization, in-memory ranking) and contain zero SQL.
* No method signature leaks a sqlite3 object, a cursor, a lock — or a
  SQL string. Row representation is an internal provider concern.
* Single-statement writes commit on their own; multi-statement
  atomicity (keyword UPSERT, turn-count bump) is internal to the
  provider and invisible to callers.
"""

from typing import TYPE_CHECKING, Any, List, Optional, Protocol, Sequence, runtime_checkable

from ..schemas import KeywordRecord, MemoryRecord, UserState

if TYPE_CHECKING:  # Phase-4/5/6 domain objects; runtime typing stays loose
    from ...experience.schemas import ExperienceRecord
    from ...reflection.schemas import ReflectionRecord
    from ...lesson.schemas import LessonRecord


@runtime_checkable
class StorageProvider(Protocol):
    """Structural contract every storage backend must satisfy.

    Implementations: ``SQLiteStorageProvider`` today, ``HermesProvider``
    in a future phase. Repositories are typed against this Protocol, so
    switching backends requires **no repository change** — only the
    composition root (``store.py``) swaps the concrete class.
    """

    # -- identity -------------------------------------------------------------

    conf_uid: str
    """Scope key this provider instance is bound to (one store per conf_uid)."""

    db_path: str
    """Location/handle of the backing store (informational; may be a URI
    for non-file backends)."""

    # -- memories aggregate ----------------------------------------------------

    def save_memory(self, record: MemoryRecord) -> None:
        """Insert or overwrite a memory row keyed by ``memory_id``."""
        ...

    def get_memory(self, memory_id: str) -> Optional[MemoryRecord]:
        """Fetch one memory by id regardless of status, or ``None``."""
        ...

    def list_memories(
        self, status: str = "active", memory_type: Optional[str] = None
    ) -> List[MemoryRecord]:
        """List memories for this conf_uid, optionally filtered by status
        and memory_type, ordered by importance then recency."""
        ...

    def list_all_memories(self) -> List[MemoryRecord]:
        """List every memory (any status) for this conf_uid."""
        ...

    def delete_memory(self, memory_id: str) -> bool:
        """Delete one memory. Returns True when a row was removed."""
        ...

    def find_active_by_content(self, content: str) -> Optional[MemoryRecord]:
        """Exact-content lookup over active memories (dedup support)."""
        ...

    def count_memories(self, status: str = "active") -> int:
        """Count memories with the given status for this conf_uid."""
        ...

    def mark_used(self, memory_ids: Sequence[str]) -> None:
        """Bump ``use_count`` / ``last_used_at`` for the given memories
        in one atomic batch."""
        ...

    # -- keywords aggregate ----------------------------------------------------

    def upsert_keyword(self, keyword: str, category: str) -> None:
        """Record one sighting of a keyword: hit_count +1 on an existing
        (keyword, category) row, insert otherwise. Atomic."""
        ...

    def list_keywords(self, limit: int = 200) -> List[KeywordRecord]:
        """Top keywords by hit_count then recency."""
        ...

    def delete_keyword(self, keyword: str, category: str) -> bool:
        """Delete one keyword row. Returns True when a row was removed."""
        ...

    def count_keywords(self) -> int:
        """Count keyword rows for this conf_uid."""
        ...

    # -- user state aggregate --------------------------------------------------

    def get_state(self) -> Optional[UserState]:
        """Load the rolling user state, or ``None`` when never saved."""
        ...

    def save_state(self, state: UserState) -> None:
        """Persist the user state row (fields written as given; timestamp
        stamping is a repository rule, not a storage one)."""
        ...

    # -- summary aggregate -----------------------------------------------------

    def get_summary(self) -> str:
        """Load the rolling conversation summary ('' when absent)."""
        ...

    def save_summary(self, text: str, turn_count: int) -> None:
        """Persist the summary text with the given turn count."""
        ...

    def get_turn_count(self) -> int:
        """Read the persisted turn count (0 when absent)."""
        ...

    def bump_turn_count(self) -> int:
        """Increment the turn count atomically and return the new value."""
        ...

    # -- experiences aggregate (Phase 4) -----------------------------------------

    def save_experience(self, record: "ExperienceRecord") -> None:
        """Insert or overwrite an experience record keyed by its
        ``experience_id`` (ExperienceRecord domain object)."""
        ...

    def get_experience(self, experience_id: str) -> Optional["ExperienceRecord"]:
        """Fetch one experience by id, or ``None``."""
        ...

    def list_experiences(self, limit: int = 200) -> List["ExperienceRecord"]:
        """Most recent experiences for this conf_uid, newest first."""
        ...

    def delete_experience(self, experience_id: str) -> bool:
        """Delete one experience. True when a row was removed."""
        ...

    def count_experiences(self) -> int:
        """Count experiences for this conf_uid."""
        ...

    # -- reflections aggregate (Phase 5) ------------------------------------------

    def save_reflection(self, record: "ReflectionRecord") -> None:
        """Insert or overwrite a reflection record keyed by its
        ``reflection_id`` (ReflectionRecord domain object)."""
        ...

    def get_reflection(self, reflection_id: str) -> Optional["ReflectionRecord"]:
        """Fetch one reflection by id, or ``None``."""
        ...

    def list_reflections(self, limit: int = 200) -> List["ReflectionRecord"]:
        """Most recent reflections for this conf_uid, newest first."""
        ...

    def delete_reflection(self, reflection_id: str) -> bool:
        """Delete one reflection. True when a row was removed."""
        ...

    def count_reflections(self) -> int:
        """Count reflections for this conf_uid."""
        ...

    # -- lessons aggregate (Phase 6) -----------------------------------------------

    def save_lesson(self, record: "LessonRecord") -> None:
        """Insert or overwrite a lesson record keyed by its ``lesson_id``
        (LessonRecord domain object)."""
        ...

    def get_lesson(self, lesson_id: str) -> Optional["LessonRecord"]:
        """Fetch one lesson by id, or ``None``."""
        ...

    def list_lessons(self, limit: int = 200) -> List["LessonRecord"]:
        """Lessons for this conf_uid, newest first."""
        ...

    def list_lessons_by_reflection(self, reflection_id: str) -> List["LessonRecord"]:
        """Lessons derived from the given reflection record."""
        ...

    def delete_lesson(self, lesson_id: str) -> bool:
        """Delete one lesson. True when a row was removed."""
        ...

    def count_lessons(self) -> int:
        """Count lessons for this conf_uid."""
        ...

    # -- lifecycle -------------------------------------------------------------

    def close(self) -> None:
        """Release the underlying connection/resources."""
        ...
