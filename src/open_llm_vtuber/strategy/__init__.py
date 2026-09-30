"""Strategy Memory (Phase 7): condition→recommendation guidelines from lessons.

A standalone domain NEXT TO memory/experience/reflection/lesson. The
engine reads LessonRecords through the lesson repository and writes
StrategyRecords through the SAME StorageProvider stack (protocol +6
strategy methods; SQLite table / Hermes sidecar like the other
LTM-only aggregates).

Boundaries (Phase-7 spec):
- Strategies guide, never command: hard commanding markers (必须/务必/
  一定要/强制/策略…) are rejected at StrategyRecord.validate();
  advisory wording IS allowed. The word 策略 itself is instruction
  phrasing and stays out of the stored text.
- Offline/batch only: no conversation hooks, nothing auto-injected
  into prompts or behavior (no Memory/Prompt/Agent changes).
- Traceability: every strategy carries source_lesson_ids (+ evidence
  subset check); a strategy without sources is rejected.
- No new config keys (spec §三.7): runtime defaults live in the
  engine/analyzer; the optional `strategy` dict is read leniently if
  a user adds one to memory_config.json.
"""

from typing import Optional

from loguru import logger

from ..long_term_memory import get_config
from ..long_term_memory.store import MemoryStore
from ..lesson.repository import LessonRepository
from .schemas import StrategyRecord
from .repository import StrategyRepository
from .engine import StrategyEngine
from .analyzer import RuleAnalyzer, LLMAnalyzer

__all__ = [
    "StrategyRecord",
    "StrategyRepository",
    "StrategyEngine",
    "RuleAnalyzer",
    "LLMAnalyzer",
    "is_enabled",
    "get_strategy_repository",
    "get_engine",
]


def is_enabled() -> bool:
    try:
        return bool(get_config().get("strategy", {}).get("enabled", True))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[STR] config read failed: {e}")
        return False


def get_strategy_repository(conf_uid: str) -> Optional[StrategyRepository]:
    """Per-conf repository over the SAME provider the memory store uses."""
    store = MemoryStore(conf_uid, config=get_config())
    return StrategyRepository(store.provider)


def get_engine(conf_uid: str, llm=None) -> Optional[StrategyEngine]:
    """Convenience composition: engine over strategy+lesson repositories."""
    if not is_enabled():
        return None
    try:
        store = MemoryStore(conf_uid, config=get_config())
        return StrategyEngine(
            StrategyRepository(store.provider),
            LessonRepository(store.provider),
            config=get_config(), llm=llm)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[STR] engine unavailable for {conf_uid}: {e}")
        return None
