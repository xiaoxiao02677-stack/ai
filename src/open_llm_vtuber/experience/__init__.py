"""Experience Memory (Phase 4): capture concrete interaction episodes.

A thin, standalone domain NEXT TO long_term_memory (never inside the
MemoryManager): the conversation layer calls one function per turn and
this package owns capture + persistence through the existing
StorageProvider architecture.

    single_conversation.py
        └─ record_turn(...)              ← the ONLY integration entry
              ExperienceEngine           (deterministic capture)
              ExperienceRepository       (domain rules: sanitize/stamp)
              StorageProvider            (existing aggregate contract)
                ├─ SQLiteStorageProvider
                └─ HermesStorageProvider

Boundaries (Phase-4 spec):
- No LLM / embedding / extra remote calls — capture is deterministic.
- No prompt injection, no retriever changes, no MemoryManager changes.
- Persistence failures degrade gracefully: logged warning, chat flow
  unaffected (same discipline as the LTM extraction hook).
- Config: ``experience.enabled`` in memory_config.json (default true;
  the flag lives in the LTM config because that file already owns
  memory-subsystem toggles — one config parser, no second system).
"""

from typing import Optional

from loguru import logger

from ..long_term_memory import get_config
from ..long_term_memory.store import MemoryStore
from .schemas import ExperienceRecord
from .repository import ExperienceRepository
from .engine import ExperienceEngine

__all__ = [
    "ExperienceRecord",
    "ExperienceEngine",
    "ExperienceRepository",
    "record_turn",
    "persist_record",
    "get_repository",
    "is_enabled",
]


def is_enabled() -> bool:
    """Capture switch (memory_config.json: experience.enabled, default on)."""
    try:
        return bool(get_config().get("experience", {}).get("enabled", True))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[XP] config read failed, capture disabled: {e}")
        return False


_repo_cache = {}


def get_repository(conf_uid: str) -> Optional[ExperienceRepository]:
    """Per-conf_uid repository over the SAME provider the memory store uses.

    One store per conf_uid (provider factory honors the storage.provider
    config, sqlite default) — experience rides the existing stack, no
    second storage system.
    """
    repo = _repo_cache.get(conf_uid)
    if repo is not None:
        return repo
    try:
        store = MemoryStore(conf_uid, config=get_config())
        repo = ExperienceRepository(store.provider)
        _repo_cache[conf_uid] = repo
        return repo
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[XP] repository unavailable for {conf_uid}: {e}")
        return None


def record_turn(
    *,
    conf_uid: str,
    history_uid: str,
    user_input: str,
    ai_response: str,
    tool_calls: Optional[list] = None,
    interaction_type: str = "chat",
    outcome_type: str = "turn_complete",
    outcome: str = "",
) -> Optional[ExperienceRecord]:
    """Capture + persist one finished interaction turn (deterministic).

    Called by the conversation layer AFTER the turn completes (fire it
    in a background task — see single_conversation.py). Returns the
    persisted record, or None when capture is disabled/unavailable.
    Failures are logged, never raised into the chat path.
    """
    engine = ExperienceEngine.start(conf_uid, history_uid, interaction_type)
    engine.record_user_input(user_input)
    for tc in tool_calls or []:
        engine.record_tool(tc.get("tool_name", ""), tc.get("status", ""))
    engine.record_ai_response(ai_response)
    engine.record_outcome(outcome)
    record = engine.finalize(outcome_type)
    return persist_record(record)


def persist_record(record: ExperienceRecord) -> Optional[ExperienceRecord]:
    """Persist an already-captured (finalized) experience record.

    This is the conversation hook's persistence path: the engine did the
    capture synchronously during the turn; this function runs in the
    background task and does repository add only. Failures are logged,
    never raised into the chat path.
    """
    if not is_enabled():
        return None
    repo = get_repository(record.conf_uid)
    if repo is None:
        logger.warning(
            f"[XP] capture dropped (no repository): {record.conf_uid}")
        return None
    try:
        repo.add(record)
        return record
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[XP] persistence failed for {record.experience_id}: {e}")
        return None
