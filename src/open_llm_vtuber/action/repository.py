"""ActionRepository: domain rules over the StorageProvider action aggregate.

Phase 10: validation-before-persist + updated_at stamping, same
discipline as the other LTM-only domain repositories. Never SQL, HTTP,
sqlite3 or hermes — and never an executor: the repository stores and
retrieves intents, nothing more.
"""

from typing import List, Optional

from ..long_term_memory.storage.provider import StorageProvider
from .schemas import ActionIntentRecord


class ActionRepository:
    """Domain access to action intents (per conf_uid)."""

    def __init__(self, provider: StorageProvider):
        self.provider = provider

    @property
    def conf_uid(self) -> str:
        return self.provider.conf_uid

    # -- write ------------------------------------------------------------------

    def save(self, record: ActionIntentRecord) -> None:
        """Validate + persist one intent (boundary rules enforced here)."""
        record.validate()
        record.touch()
        self.provider.save_action(record)

    # -- read -------------------------------------------------------------------

    def get(self, action_id: str) -> Optional[ActionIntentRecord]:
        return self.provider.get_action(action_id)

    def list_actions(self, limit: int = 200) -> List[ActionIntentRecord]:
        """Intents for this conf_uid, newest first."""
        return self.provider.list_actions(limit=limit)

    def list_by_decision(self, decision_id: str) -> List[ActionIntentRecord]:
        """Intents derived from the given decision (reverse trace)."""
        return self.provider.list_actions_by_decision(decision_id)

    def list_by_evaluation(self, evaluation_id: str) -> List[ActionIntentRecord]:
        """Intents derived from evaluations of the chosen strategy line."""
        return self.provider.list_actions_by_evaluation(evaluation_id)

    def delete(self, action_id: str) -> bool:
        return self.provider.delete_action(action_id)

    def count(self) -> int:
        return self.provider.count_actions()
