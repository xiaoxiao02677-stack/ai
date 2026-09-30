"""EvaluationRepository: domain rules over the StorageProvider evaluation aggregate.

Phase 8: validation-before-persist + updated_at stamping, same
discipline as the other LTM-only domain repositories. Never SQL, HTTP,
sqlite3 or hermes.
"""

from typing import List, Optional

from ..long_term_memory.storage.provider import StorageProvider
from .schemas import EvaluationRecord


class EvaluationRepository:
    """Domain access to strategy applicability evaluations (per conf_uid)."""

    def __init__(self, provider: StorageProvider):
        self.provider = provider

    @property
    def conf_uid(self) -> str:
        return self.provider.conf_uid

    # -- write ------------------------------------------------------------------

    def save(self, record: EvaluationRecord) -> None:
        """Validate + persist one evaluation (boundary rules enforced here)."""
        record.validate()
        record.touch()
        self.provider.save_evaluation(record)

    # -- read -------------------------------------------------------------------

    def get(self, evaluation_id: str) -> Optional[EvaluationRecord]:
        return self.provider.get_evaluation(evaluation_id)

    def list_evaluations(self, limit: int = 200) -> List[EvaluationRecord]:
        """Evaluations for this conf_uid, newest first."""
        return self.provider.list_evaluations(limit=limit)

    def list_by_strategy(self, strategy_id: str) -> List[EvaluationRecord]:
        """All evaluations of one strategy (reverse trace)."""
        return self.provider.list_evaluations_by_strategy(strategy_id)

    def delete(self, evaluation_id: str) -> bool:
        return self.provider.delete_evaluation(evaluation_id)

    def count(self) -> int:
        return self.provider.count_evaluations()
