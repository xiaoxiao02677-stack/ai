"""Execution (Phase 11): sandbox-simulated action execution results.

A standalone domain completing the eight-layer chain:
experience → reflection → lesson → strategy → evaluation → decision →
action intent → sandbox execution → STOP.

The SandboxExecutor is the security boundary: it re-validates every
intent (status/type/parameters/provenance/conf) against independent
whitelists and a STRICT per-type parameter schema (closed structs —
the Phase-10 LOW finding upgraded as required), then runs a purely
deterministic simulation. There is no real mode, no network, no
filesystem write, no dynamic execution — execution_mode is a
single-value enum (SANDBOX) by construction.

Boundaries (Phase-11 spec):
- SIMULATED results carry result.simulated=true; internal failures are
  FAILED, never fake-simulated.
- The engine verifies provenance against STORED records (hand-crafted
  intents cannot pass) and never mutates the ActionIntent.
- Repeated execution creates new timestamped results (history by
  design, consistent with Phases 8-10).
- No new config keys; no conversation/memory/prompt/agent hooks.
"""

from typing import Optional

from loguru import logger

from ..long_term_memory import get_config
from ..long_term_memory.store import MemoryStore
from ..action.repository import ActionRepository
from ..decision.repository import DecisionRepository
from ..evaluation.repository import EvaluationRepository
from .schemas import ExecutionResult
from .repository import ExecutionRepository
from .engine import ExecutionEngine
from .sandbox import SandboxExecutor

__all__ = [
    "ExecutionResult",
    "ExecutionRepository",
    "ExecutionEngine",
    "SandboxExecutor",
    "is_enabled",
    "get_execution_repository",
    "get_engine",
]


def is_enabled() -> bool:
    try:
        return bool(get_config().get("execution", {}).get("enabled", True))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[SBX] config read failed: {e}")
        return False


def get_execution_repository(conf_uid: str) -> Optional[ExecutionRepository]:
    """Per-conf repository over the SAME provider the memory store uses."""
    store = MemoryStore(conf_uid, config=get_config())
    return ExecutionRepository(store.provider)


def get_engine(conf_uid: str) -> Optional[ExecutionEngine]:
    """Convenience composition: engine over execution+action+decision+
    evaluation repositories (all conf-scoped on the same provider)."""
    if not is_enabled():
        return None
    try:
        store = MemoryStore(conf_uid, config=get_config())
        provider = store.provider
        return ExecutionEngine(
            ExecutionRepository(provider),
            ActionRepository(provider),
            DecisionRepository(provider),
            EvaluationRepository(provider),
            config=get_config())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[SBX] engine unavailable for {conf_uid}: {e}")
        return None
