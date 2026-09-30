"""DecisionRepository: domain rules over the StorageProvider decision aggregate.

Phase 9: validation-before-persist + updated_at stamping, same
discipline as the other LTM-only domain repositories. Never SQL, HTTP,
sqlite3 or hermes.
"""

from typing import List, Optional

from ..long_term_memory.storage.provider import StorageProvider
from .schemas import DecisionRecord


class DecisionRepository:
    """Domain access to structured decisions (per conf_uid)."""

    def __init__(self, provider: StorageProvider):
        self.provider = provider

    @property
    def conf_uid(self) -> str:
        return self.provider.conf_uid

    # -- write ------------------------------------------------------------------

    def save(self, record: DecisionRecord) -> None:
        """Validate + persist one decision (boundary rules enforced here)."""
        record.validate()
        record.touch()
        self.provider.save_decision(record)

    # -- read -------------------------------------------------------------------

    def get(self, decision_id: str) -> Optional[DecisionRecord]:
        return self.provider.get_decision(decision_id)

    def list_decisions(self, limit: int = 200) -> List[DecisionRecord]:
        """Decisions for this conf_uid, newest first."""
        return self.provider.list_decisions(limit=limit)

    def list_by_strategy(self, strategy_id: str) -> List[DecisionRecord]:
        """Decisions that selected (or considered) the given strategy."""
        return self.provider.list_decisions_by_strategy(strategy_id)

    def list_by_evaluation(self, evaluation_id: str) -> List[DecisionRecord]:
        """Decisions derived from the given evaluation (reverse trace)."""
        return self.provider.list_decisions_by_evaluation(evaluation_id)

    def delete(self, decision_id: str) -> bool:
        return self.provider.delete_decision(decision_id)

    def count(self) -> int:
        return self.provider.count_decisions()
