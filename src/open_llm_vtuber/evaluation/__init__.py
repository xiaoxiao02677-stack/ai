"""Evaluation (Phase 8): strategy applicability judgments.

A standalone domain NEXT TO memory/experience/reflection/lesson/
strategy. The engine reads StrategyRecords through the strategy
repository and writes EvaluationRecords through the SAME
StorageProvider stack (protocol +6 evaluation methods; SQLite table /
Hermes sidecar like the other LTM-only aggregates).

Boundaries (Phase-8 spec):
- Evaluation is a JUDGMENT, never an action: no execution, no
  injection, no mutation downstream. Pure data interface for future
  decision layers.
- Zero conversation hooks: single_conversation.py / MemoryManager /
  MemoryRetriever / prompts are untouched (scan-tested).
- Provenance: every evaluation carries strategy_id; reverse trace
  evaluation -> strategy -> lesson -> reflection -> experience.
- No new config keys: runtime defaults live in the engine/analyzer
  (same as Phase 7); optional evaluation dict read leniently.
- Non-silent fallback: LLM failure degrades to the rule layer with a
  logged INFO — explicit, same-domain, test-covered.
"""

from typing import Optional

from loguru import logger

from ..long_term_memory import get_config
from ..long_term_memory.store import MemoryStore
from ..strategy.repository import StrategyRepository
from .schemas import EvaluationRecord
from .repository import EvaluationRepository
from .engine import EvaluationEngine
from .analyzer import RuleAnalyzer, LLMAnalyzer

__all__ = [
    "EvaluationRecord",
    "EvaluationRepository",
    "EvaluationEngine",
    "RuleAnalyzer",
    "LLMAnalyzer",
    "is_enabled",
    "get_evaluation_repository",
    "get_engine",
]


def is_enabled() -> bool:
    try:
        return bool(get_config().get("evaluation", {}).get("enabled", True))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[EVL] config read failed: {e}")
        return False


def get_evaluation_repository(conf_uid: str) -> Optional[EvaluationRepository]:
    """Per-conf repository over the SAME provider the memory store uses."""
    store = MemoryStore(conf_uid, config=get_config())
    return EvaluationRepository(store.provider)


def get_engine(conf_uid: str, llm=None) -> Optional[EvaluationEngine]:
    """Convenience composition: engine over evaluation+strategy repositories."""
    if not is_enabled():
        return None
    try:
        store = MemoryStore(conf_uid, config=get_config())
        return EvaluationEngine(
            EvaluationRepository(store.provider),
            StrategyRepository(store.provider),
            config=get_config(), llm=llm)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[EVL] engine unavailable for {conf_uid}: {e}")
        return None
