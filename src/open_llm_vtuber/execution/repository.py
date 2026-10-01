"""ExecutionRepository: domain rules over the StorageProvider execution aggregate.

Phase 11: validation-before-persist + updated_at stamping, same
discipline as the other LTM-only domain repositories. Never SQL, HTTP,
sqlite3 or hermes — and never an executor.
"""

from typing import List, Optional

from ..long_term_memory.storage.provider import StorageProvider
from .schemas import ExecutionResult


class ExecutionRepository:
    """Domain access to sandbox execution results (per conf_uid)."""

    def __init__(self, provider: StorageProvider):
        self.provider = provider

    @property
    def conf_uid(self) -> str:
        return self.provider.conf_uid

    # -- write ------------------------------------------------------------------

    def save(self, record: ExecutionResult) -> None:
        """Validate + persist one execution result."""
        record.validate()
        record.touch()
        self.provider.save_execution(record)

    # -- read -------------------------------------------------------------------

    def get(self, execution_id: str) -> Optional[ExecutionResult]:
        return self.provider.get_execution(execution_id)

    def list_executions(self, limit: int = 200) -> List[ExecutionResult]:
        """Execution results for this conf_uid, newest first."""
        return self.provider.list_executions(limit=limit)

    def list_by_action(self, action_id: str) -> List[ExecutionResult]:
        """Results of one action intent (reverse trace)."""
        return self.provider.list_executions_by_action(action_id)

    def list_by_decision(self, decision_id: str) -> List[ExecutionResult]:
        """Results derived from a given decision line (reverse trace)."""
        return self.provider.list_executions_by_decision(decision_id)

    def delete(self, execution_id: str) -> bool:
        return self.provider.delete_execution(execution_id)

    def count(self) -> int:
        return self.provider.count_executions()
