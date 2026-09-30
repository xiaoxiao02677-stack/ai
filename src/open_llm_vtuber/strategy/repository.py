"""StrategyRepository: domain rules over the StorageProvider strategy aggregate.

Phase 7: validation-before-persist + updated_at stamping, same
discipline as the other LTM-only domain repositories. Never SQL, HTTP,
sqlite3 or hermes.
"""

from typing import List, Optional

from ..long_term_memory.storage.provider import StorageProvider
from .schemas import StrategyRecord


class StrategyRepository:
    """Domain access to derived strategies (per conf_uid)."""

    def __init__(self, provider: StorageProvider):
        self.provider = provider

    @property
    def conf_uid(self) -> str:
        return self.provider.conf_uid

    # -- write ------------------------------------------------------------------

    def save(self, record: StrategyRecord) -> None:
        """Validate + persist one strategy (boundary rules enforced here)."""
        record.validate()
        record.touch()
        self.provider.save_strategy(record)

    # -- read -------------------------------------------------------------------

    def get(self, strategy_id: str) -> Optional[StrategyRecord]:
        return self.provider.get_strategy(strategy_id)

    def list_strategies(self, limit: int = 200) -> List[StrategyRecord]:
        """Strategies for this conf_uid, newest first."""
        return self.provider.list_strategies(limit=limit)

    def list_by_lesson(self, lesson_id: str) -> List[StrategyRecord]:
        """Strategies derived from the given lesson record."""
        return self.provider.list_strategies_by_lesson(lesson_id)

    def delete(self, strategy_id: str) -> bool:
        return self.provider.delete_strategy(strategy_id)

    def count(self) -> int:
        return self.provider.count_strategies()
