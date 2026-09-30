"""Decision (Phase 9): structured, explainable choices over evaluations.

A standalone domain completing the six-layer distillation chain:
experience → reflection → lesson → strategy → evaluation → decision.
The engine reads EvaluationRecords through the evaluation repository
(never strategies directly) and writes DecisionRecords through the
SAME StorageProvider stack (protocol +7 decision methods; SQLite
table / Hermes sidecar like the other LTM-only aggregates).

Boundaries (Phase-9 spec):
- Decision != Action: the chain ENDS here. No tool calls, no HTTP, no
  messages, no prompt/memory/personality/conversation changes.
- Abstain is a first-class outcome: no applicable candidate, low
  confidence, or ambiguous ties all yield status=abstain instead of a
  forced pick.
- Provenance: selected decisions carry BOTH selected_evaluation_id
  and selected_strategy_id, validated to agree with each other.
- No new config keys (runtime defaults live in the analyzer).
"""

from typing import Optional

from loguru import logger

from ..long_term_memory import get_config
from ..long_term_memory.store import MemoryStore
from ..evaluation.repository import EvaluationRepository
from .schemas import DecisionRecord
from .repository import DecisionRepository
from .engine import DecisionEngine
from .analyzer import RuleDecisionAnalyzer, LLMDecisionAnalyzer

__all__ = [
    "DecisionRecord",
    "DecisionRepository",
    "DecisionEngine",
    "RuleDecisionAnalyzer",
    "LLMDecisionAnalyzer",
    "is_enabled",
    "get_decision_repository",
    "get_engine",
]


def is_enabled() -> bool:
    try:
        return bool(get_config().get("decision", {}).get("enabled", True))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[DEC] config read failed: {e}")
        return False


def get_decision_repository(conf_uid: str) -> Optional[DecisionRepository]:
    """Per-conf repository over the SAME provider the memory store uses."""
    store = MemoryStore(conf_uid, config=get_config())
    return DecisionRepository(store.provider)


def get_engine(conf_uid: str, llm=None) -> Optional[DecisionEngine]:
    """Convenience composition: engine over decision+evaluation repositories."""
    if not is_enabled():
        return None
    try:
        store = MemoryStore(conf_uid, config=get_config())
        return DecisionEngine(
            DecisionRepository(store.provider),
            EvaluationRepository(store.provider),
            config=get_config(), llm=llm)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[DEC] engine unavailable for {conf_uid}: {e}")
        return None
