"""Reflection Memory (Phase 5): fact-only observations over experiences.

A standalone domain NEXT TO long_term_memory and experience — reflection
reads Experience through ExperienceRepository and writes
ReflectionRecords through the SAME StorageProvider stack (protocol +5
reflection methods; SQLite table / Hermes sidecar like experiences).

Boundaries (Phase-5 spec):
- Observation only: no lessons, strategies, "should do" language —
  enforced at ReflectionRecord.validate() and rejected on save.
- Never called from the conversation path: analysis is offline/batch
  (callers: manual command / scheduled job / tests).
- Zero changes to Agent core, prompts, MemoryManager, MemoryRetriever,
  ExperienceEngine, single_conversation.py.
- Config block (memory_config.json): reflection.enabled (capture/persist
  switch), reflection.llm_analysis (LLM refinement toggle, default off
  until an LLM is attached), reflection.batch_size.
"""

from typing import Optional

from loguru import logger

from ..long_term_memory import get_config
from ..long_term_memory.store import MemoryStore
from ..experience.repository import ExperienceRepository
from .schemas import ReflectionRecord
from .repository import ReflectionRepository
from .engine import ReflectionEngine
from .analyzer import RuleAnalyzer, LLMAnalyzer

__all__ = [
    "ReflectionRecord",
    "ReflectionRepository",
    "ReflectionEngine",
    "RuleAnalyzer",
    "LLMAnalyzer",
    "is_enabled",
    "get_reflection_repository",
    "get_experience_repository",
    "get_engine",
]


def is_enabled() -> bool:
    try:
        return bool(get_config().get("reflection", {}).get("enabled", True))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[RFL] config read failed: {e}")
        return False


def get_reflection_repository(conf_uid: str) -> Optional[ReflectionRepository]:
    """Per-conf repository over the SAME provider the memory store uses."""
    store = MemoryStore(conf_uid, config=get_config())
    return ReflectionRepository(store.provider)


def get_experience_repository(conf_uid: str) -> Optional[ExperienceRepository]:
    """Experience read-side for the same conf (reuses the store provider)."""
    store = MemoryStore(conf_uid, config=get_config())
    return ExperienceRepository(store.provider)


def get_engine(conf_uid: str, llm=None) -> Optional[ReflectionEngine]:
    """Convenience composition: engine over both repositories."""
    if not is_enabled():
        return None
    try:
        store = MemoryStore(conf_uid, config=get_config())
        return ReflectionEngine(
            ReflectionRepository(store.provider),
            ExperienceRepository(store.provider),
            config=get_config(), llm=llm)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[RFL] engine unavailable for {conf_uid}: {e}")
        return None
