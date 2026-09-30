"""ReflectionRepository: domain rules over the StorageProvider reflection aggregate.

Phase 5: same discipline as Memory/Experience repositories — the repo
owns domain rules (validation before persist, updated_at stamping), the
provider owns storage. Never SQL, never HTTP, never hermes/sqlite3.
"""

from typing import List, Optional

from ..long_term_memory.storage.provider import StorageProvider
from .schemas import ReflectionRecord


class ReflectionRepository:
    """Domain access to derived reflection observations (per conf_uid)."""

    def __init__(self, provider: StorageProvider):
        self.provider = provider

    @property
    def conf_uid(self) -> str:
        return self.provider.conf_uid

    # -- write ------------------------------------------------------------------

    def save(self, record: ReflectionRecord) -> None:
        """Validate + persist one reflection (boundary rules enforced here)."""
        record.validate()
        record.touch()
        self.provider.save_reflection(record)

    # -- read -------------------------------------------------------------------

    def get(self, reflection_id: str) -> Optional[ReflectionRecord]:
        return self.provider.get_reflection(reflection_id)

    def list_recent(self, limit: int = 50,
                    reflection_type: Optional[str] = None) -> List[ReflectionRecord]:
        """Most recent reflections for this conf_uid, newest first."""
        recs = self.provider.list_reflections(limit=max(limit, 1) * 3)
        if reflection_type:
            recs = [r for r in recs if r.reflection_type == reflection_type]
        return recs[:limit]

    def list_by_conf_uid(self) -> List[ReflectionRecord]:
        """All reflections for this conf_uid (provider is conf-scoped)."""
        return self.provider.list_reflections(limit=10000)

    def delete(self, reflection_id: str) -> bool:
        return self.provider.delete_reflection(reflection_id)

    def count(self) -> int:
        return self.provider.count_reflections()
