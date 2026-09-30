"""Action Intent (Phase 10): side-effect-free intents from decisions.

A standalone domain completing the seven-layer chain:
experience → reflection → lesson → strategy → evaluation → decision →
action intent. The engine derives intents ONLY from selected
decisions (provenance verified at every hop) and persists them
through the SAME StorageProvider stack (protocol +6 action methods;
SQLite table / Hermes sidecar like the other LTM-only aggregates).

Boundaries (Phase-10 spec):
- ActionIntent is a RECORD of intent, never an executor: no tools,
  MCP, agents, HTTP, shell, devices, messages — nothing is performed,
  dispatched or invoked. The chain ends here.
- Provenance: decision/evaluation/strategy ids all derive from the
  decision record (never an LLM); evaluation_id/strategy_id must
  equal the decision's selected ids.
- Controlled vocabulary: RESPOND / REMIND / ACKNOWLEDGE (abstract
  intents); execution types are structurally absent.
- Parameters are data: executable keys/values are blacklisted and
  rejected at validation.
- No new config keys; no conversation/memory/prompt/agent hooks.
"""

from typing import Optional

from loguru import logger

from ..long_term_memory import get_config
from ..long_term_memory.store import MemoryStore
from ..decision.repository import DecisionRepository
from ..evaluation.repository import EvaluationRepository
from ..strategy.repository import StrategyRepository
from .schemas import ActionIntentRecord
from .repository import ActionRepository
from .engine import ActionEngine
from .analyzer import RuleActionAnalyzer, LLMActionAnalyzer

__all__ = [
    "ActionIntentRecord",
    "ActionRepository",
    "ActionEngine",
    "RuleActionAnalyzer",
    "LLMActionAnalyzer",
    "is_enabled",
    "get_action_repository",
    "get_engine",
]


def is_enabled() -> bool:
    try:
        return bool(get_config().get("action", {}).get("enabled", True))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ACT] config read failed: {e}")
        return False


def get_action_repository(conf_uid: str) -> Optional[ActionRepository]:
    """Per-conf repository over the SAME provider the memory store uses."""
    store = MemoryStore(conf_uid, config=get_config())
    return ActionRepository(store.provider)


def get_engine(conf_uid: str, llm=None) -> Optional[ActionEngine]:
    """Convenience composition: engine over action+decision+evaluation+
    strategy repositories (all conf-scoped on the same provider)."""
    if not is_enabled():
        return None
    try:
        store = MemoryStore(conf_uid, config=get_config())
        provider = store.provider
        return ActionEngine(
            ActionRepository(provider),
            DecisionRepository(provider),
            EvaluationRepository(provider),
            StrategyRepository(provider),
            config=get_config(), llm=llm)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ACT] engine unavailable for {conf_uid}: {e}")
        return None
