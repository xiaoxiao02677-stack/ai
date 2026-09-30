"""ExperienceRepository: domain rules over the StorageProvider experience aggregate.

Phase 4: persistence delegation only, mirroring the LTM repository
style — the repo owns domain rules (privacy sanitization at capture,
updated_at stamping, dedup-by-id semantics), the provider owns storage.
The repo never sees SQL, HTTP, sqlite3 or hermes; it is typed against
the aggregate ``StorageProvider`` Protocol (LTM storage subpackage).

Privacy: user_input / ai_response / outcome run through the LTM
``sanitize_memory_text`` (length cap + sensitive-content masking) — the
same policy Memory extraction already uses. Zero new privacy rules.
"""

import time
from typing import List, Optional

from ..long_term_memory.privacy import (
    privacy_check,
    sanitize_memory_text,
)
from ..long_term_memory.storage.provider import StorageProvider
from .schemas import ExperienceRecord

# capture-time caps: an Experience is an episode summary, not a chat log
_MAX_FIELD_LEN = 600


def _privacy_gate(text: str, record: ExperienceRecord, field: str) -> str:
    """Apply the LTM privacy policy to one capture field.

    Same semantics as memory extraction: ``privacy_check`` failing means
    DO NOT STORE — the field is dropped (never stored masked or partial,
    never fabricated) and the drop is recorded in metadata so the episode
    stays auditable.
    """
    verdict = privacy_check(text)
    if not verdict["ok"]:
        record.metadata.setdefault("privacy_dropped", []).append(
            {"field": field, "reason": verdict["reason"]})
        return ""
    return sanitize_memory_text(text, _MAX_FIELD_LEN)


class ExperienceRepository:
    """Domain access to captured interaction experiences (per conf_uid)."""

    def __init__(self, provider: StorageProvider):
        self.provider = provider

    @property
    def conf_uid(self) -> str:
        return self.provider.conf_uid

    # -- write ------------------------------------------------------------------

    def add(self, record: ExperienceRecord) -> None:
        """Persist a finalized experience (privacy-gated + stamped)."""
        if not record.experience_id:
            raise ValueError("experience record has no experience_id")
        record.user_input = _privacy_gate(record.user_input, record, "user_input")
        record.ai_response = _privacy_gate(record.ai_response, record, "ai_response")
        record.outcome = sanitize_memory_text(record.outcome, 200)
        record.touch()
        self.provider.save_experience(record)

    def update(self, record: ExperienceRecord) -> None:
        """Overwrite an existing experience by id (re-finalize path)."""
        record.touch()
        self.provider.save_experience(record)

    # -- read -------------------------------------------------------------------

    def get(self, experience_id: str) -> Optional[ExperienceRecord]:
        return self.provider.get_experience(experience_id)

    def list_recent(self, limit: int = 50,
                    interaction_type: Optional[str] = None) -> List[ExperienceRecord]:
        """Most recent finalized experiences, newest first (conf_uid scoped)."""
        recs = self.provider.list_experiences(limit=max(limit, 1) * 3)
        if interaction_type:
            recs = [r for r in recs if r.interaction_type == interaction_type]
        return recs[:limit]

    def list_by_history(self, history_uid: str) -> List[ExperienceRecord]:
        """All experiences of one conversation session (conf_uid scoped)."""
        return [r for r in self.provider.list_experiences(limit=1000)
                if r.history_uid == history_uid]

    def delete(self, experience_id: str) -> bool:
        return self.provider.delete_experience(experience_id)

    def count(self) -> int:
        return self.provider.count_experiences()
