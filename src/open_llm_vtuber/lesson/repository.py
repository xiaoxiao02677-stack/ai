"""LessonRepository: domain rules over the StorageProvider lesson aggregate.

Phase 6: validation-before-persist + updated_at stamping, same
discipline as the Memory/Experience/Reflection repositories. The repo
never sees SQL, HTTP, sqlite3 or hermes.
"""

from typing import List, Optional

from ..long_term_memory.storage.provider import StorageProvider
from .schemas import LessonRecord


class LessonRepository:
    """Domain access to distilled lessons (per conf_uid)."""

    def __init__(self, provider: StorageProvider):
        self.provider = provider

    @property
    def conf_uid(self) -> str:
        return self.provider.conf_uid

    # -- write ------------------------------------------------------------------

    def save(self, record: LessonRecord) -> None:
        """Validate + persist one lesson (boundary rules enforced here)."""
        record.validate()
        record.touch()
        self.provider.save_lesson(record)

    # -- read -------------------------------------------------------------------

    def get(self, lesson_id: str) -> Optional[LessonRecord]:
        return self.provider.get_lesson(lesson_id)

    def list_lessons(self, limit: int = 200) -> List[LessonRecord]:
        """Lessons for this conf_uid, newest first."""
        return self.provider.list_lessons(limit=limit)

    def list_by_reflection(self, reflection_id: str) -> List[LessonRecord]:
        """Lessons derived from a given reflection record."""
        return self.provider.list_lessons_by_reflection(reflection_id)

    def delete(self, lesson_id: str) -> bool:
        return self.provider.delete_lesson(lesson_id)

    def count(self) -> int:
        return self.provider.count_lessons()
