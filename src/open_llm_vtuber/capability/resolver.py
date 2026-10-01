"""CapabilityResolver: deterministic action_type -> capability contract.

The resolver answers ONE question: "which capability contract does
this action type map to?" It is a PURE LOOKUP — deterministic (same
action_type, same contract, always), no LLM, no network discovery,
no time/environment-dependent routing, no fallbacks (a disabled
capability resolves to DISABLED, never to a substitute).

It does not judge whether an action is WORTH executing (that was the
Evaluation/Decision layers' job) and it never executes anything.
"""

from typing import Dict

from .registry import CapabilityRegistry, DEFAULT_REGISTRY
from .schemas import (CapabilityContract, ResolutionResult,
                      RESOLVED, UNSUPPORTED, DISABLED, INVALID)


class CapabilityResolver:
    """Deterministic ActionType -> CapabilityContract mapping."""

    def __init__(self, registry: CapabilityRegistry = None):
        self.registry = registry if registry is not None else DEFAULT_REGISTRY

    def _by_action_type(self) -> Dict[str, CapabilityContract]:
        """action_type -> contract map (rebuilt deterministically)."""
        out: Dict[str, CapabilityContract] = {}
        for contract in self.registry.list_contracts():
            for at in contract.allowed_action_types:
                # first (deterministic, id-sorted) contract wins per type
                if at not in out:
                    out[at] = contract
        return out

    def resolve(self, action_type: str) -> ResolutionResult:
        """Resolve one action type. Pure, total, deterministic."""
        if not action_type or not isinstance(action_type, str):
            return ResolutionResult(
                status=INVALID, reason="action_type must be a non-empty string")
        contract = self._by_action_type().get(action_type)
        if contract is None:
            return ResolutionResult(
                status=UNSUPPORTED,
                reason=f"no capability registered for '{action_type}'")
        if not contract.enabled:
            return ResolutionResult(
                status=DISABLED, capability=None,
                capability_id=contract.capability_id,
                reason=f"capability '{contract.capability_id}' is disabled "
                       f"(no fallback — semantics cannot change)")
        return ResolutionResult(
            status=RESOLVED, capability=contract,
            capability_id=contract.capability_id,
            reason=f"resolved to {contract.capability_id} "
                   f"v{contract.version}")
