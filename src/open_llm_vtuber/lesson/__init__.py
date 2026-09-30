"""Lesson Memory (Phase 6): reusable takeaways distilled from reflections.

A standalone domain NEXT TO memory/experience/reflection. The engine
reads ReflectionRecords through the reflection repository and writes
LessonRecords through the SAME StorageProvider stack (protocol +6
lesson methods; SQLite table / Hermes sidecar like the other LTM-only
aggregates).

Boundaries (Phase-6 spec):
- Lessons inform, never command: hard-policy markers (必须/must/
  always/策略…) are rejected at LessonRecord.validate(); advisory
  wording IS allowed (a lesson is advisory by nature).
- Offline/batch only: no conversation hooks, nothing auto-injected
  into prompts or behavior (no Memory/Prompt/Agent changes).
- Traceability: every lesson carries source_reflection_ids.
- Config block (memory_config.json): lesson.enabled / llm_analysis /
  batch_size / min_support / llm_timeout.
"""

from typing import Optional

from loguru import logger

from ..long_term_memory import get_config
from ..long_term_memory.store import MemoryStore
from ..reflection.repository import ReflectionRepository
from .schemas import LessonRecord
from .repository import LessonRepository
from .engine import LessonEngine
from .analyzer import RuleAnalyzer, LLMAnalyzer

__all__ = [
    "LessonRecord",
    "LessonRepository",
    "LessonEngine",
    "RuleAnalyzer",
    "LLMAnalyzer",
    "is_enabled",
    "get_lesson_repository",
    "get_engine",
]


def is_enabled() -> bool:
    try:
        return bool(get_config().get("lesson", {}).get("enabled", True))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[LSN] config read failed: {e}")
        return False


def get_lesson_repository(conf_uid: str) -> Optional[LessonRepository]:
    """Per-conf repository over the SAME provider the memory store uses."""
    store = MemoryStore(conf_uid, config=get_config())
    return LessonRepository(store.provider)


def get_engine(conf_uid: str, llm=None) -> Optional[LessonEngine]:
    """Convenience composition: engine over lesson+reflection repositories."""
    if not is_enabled():
        return None
    try:
        store = MemoryStore(conf_uid, config=get_config())
        return LessonEngine(
            LessonRepository(store.provider),
            ReflectionRepository(store.provider),
            config=get_config(), llm=llm)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[LSN] engine unavailable for {conf_uid}: {e}")
        return None
