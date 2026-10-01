"""Capability (Phase 12): static capability contracts + deterministic resolver.

A standalone, process-scoped domain NEXT TO the eight-layer chain:

    ActionIntent -> CapabilityResolver -> CapabilityContract
                 -> SandboxExecutor -> ExecutionResult -> STOP

Design decisions (spec §25/§26): contracts are STATIC SYSTEM
DEFINITIONS (what the system can describe), not user experience data —
they are NOT part of the Experience/…/ExecutionResult record chain and
therefore DO NOT enter StorageProvider (no SQLite/Hermes tables, no
conf_uid scoping). The registry is frozen after construction; runtime
registration is refused.

Boundaries (Phase-12 spec):
- Contracts are data descriptions, never executables: no handler/
  function/callback/URL/command fields (validated).
- The resolver is a pure deterministic lookup: no LLM, no fallbacks,
  no network/time-dependent routing; disabled -> DISABLED (never a
  substitute capability).
- Zero real side effects: no tools/MCP/agents/HTTP/devices/shell.
"""

from .registry import CapabilityRegistry, DEFAULT_REGISTRY
from .resolver import CapabilityResolver
from .schemas import (CapabilityContract, ResolutionResult,
                      RESOLVED, UNSUPPORTED, DISABLED, INVALID)

__all__ = [
    "CapabilityContract",
    "ResolutionResult",
    "CapabilityRegistry",
    "CapabilityResolver",
    "DEFAULT_REGISTRY",
    "RESOLVED", "UNSUPPORTED", "DISABLED", "INVALID",
    "get_resolver",
]


def get_resolver() -> CapabilityResolver:
    """Process-scoped resolver over the default (builtin) registry."""
    return CapabilityResolver(DEFAULT_REGISTRY)
