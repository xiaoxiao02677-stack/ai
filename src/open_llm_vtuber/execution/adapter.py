"""ExecutionAdapter interface + static registry + Fake adapter.

The adapter is the LAST hop of the execution chain: it receives an
already-validated, already-authorized ExecutionRequest (a frozen
snapshot of the intent + contract + policy context) and produces the
execution payload for the ExecutionResult. Adapters do NOT decide,
evaluate, select capabilities, or check permissions — that all
happened upstream (Decision/Resolver/Policy).

Static registry discipline (mirrors Phase 12's CapabilityRegistry):
frozen after construction, duplicates error, no runtime registration,
no dynamic loading, no string-built targets. Phase 13 registers
exactly ONE adapter: the FakeExecutionAdapter (PURE side-effect
level, deterministic, zero external effects).
"""

from typing import Any, Dict, Optional

from ..action.schemas import ActionIntentRecord
from ..capability.schemas import CapabilityContract
from .schemas import ExecutionResult, STATUS_SIMULATED, STATUS_REJECTED


class ExecutionRequest:
    """Frozen view of everything an adapter may see.

    Built by the gateway AFTER provenance/policy checks — adapters
    never touch the ActionIntent object itself (immutability), only
    this read-only snapshot.
    """

    def __init__(self, *, conf_uid: str, intent: ActionIntentRecord,
                 contract: CapabilityContract, policy_status: str,
                 adapter_id: str):
        self.conf_uid = conf_uid
        self.action_id = intent.action_id
        self.decision_id = intent.decision_id
        self.evaluation_id = intent.evaluation_id
        self.strategy_id = intent.strategy_id
        self.action_type = intent.action_type
        # parameters already validated against the contract's closed
        # schema upstream; passed as a copied dict
        self.parameters = dict(intent.parameters)
        self.capability_id = contract.capability_id
        self.capability_version = contract.version
        self.policy_status = policy_status
        self.adapter_id = adapter_id


class ExecutionAdapter:
    """Interface: deterministic execution payload producer.

    Subclasses declare adapter_id, capability_id, side_effect_level and
    implement run(request) -> (payload_dict, reason). They must NEVER
    perform side effects unless a future phase explicitly builds a real
    adapter — Phase 13 ships only the fake one.
    """

    adapter_id: str = ""
    capability_id: str = ""
    side_effect_level: str = "PURE"

    def run(self, request: ExecutionRequest) -> Dict[str, Any]:
        raise NotImplementedError


class FakeExecutionAdapter(ExecutionAdapter):
    """Deterministic, side-effect-free adapter (the ONLY Phase 13 adapter).

    Produces an explicit simulated payload: no network, no subprocess,
    no shell, no GPIO/serial/MQTT, no filesystem writes, no external
    APIs. Identical requests yield identical payloads (pure function).
    """

    side_effect_level = "PURE"   # class-level: subclasses may declare a
                                 # different level and the policy will
                                 # decide on it (deny by default)

    def __init__(self, capability_id: str):
        self.adapter_id = f"fake.{capability_id.split('.', 1)[-1]}"
        self.capability_id = capability_id

    def run(self, request: ExecutionRequest) -> Dict[str, Any]:
        # pure function of the request — deterministic by construction
        return {
            "simulated": True,
            "action_type": request.action_type,
            "capability_id": request.capability_id,
            "capability_version": request.capability_version,
            "parameters": dict(request.parameters),
        }


class AdapterRegistry:
    """Static, frozen adapter registry (keyed by capability_id)."""

    def __init__(self, adapters: Optional[Dict[str, ExecutionAdapter]] = None):
        self._adapters: Dict[str, ExecutionAdapter] = dict(adapters or {})
        self._frozen = True

    def get(self, capability_id: str) -> Optional[ExecutionAdapter]:
        return self._adapters.get(capability_id)

    def list_ids(self) -> list:
        return sorted(self._adapters.keys())

    def register(self, adapter: ExecutionAdapter) -> None:
        if self._frozen:
            raise RuntimeError(
                "AdapterRegistry is frozen — runtime registration is not "
                "allowed (adapters are static system definitions)")
        if adapter.capability_id in self._adapters:
            raise ValueError(
                f"duplicate adapter for capability '{adapter.capability_id}'")
        self._adapters[adapter.capability_id] = adapter

    def _unfreeze_for_audit(self) -> None:
        self._frozen = False

    def _refreeze(self) -> None:
        self._frozen = True


def _builtin_adapters() -> Dict[str, ExecutionAdapter]:
    # exactly one adapter per Phase-12 builtin capability — all fake/PURE
    return {
        "capability.respond": FakeExecutionAdapter("capability.respond"),
        "capability.remind": FakeExecutionAdapter("capability.remind"),
        "capability.acknowledge": FakeExecutionAdapter("capability.acknowledge"),
    }


# process-scoped default registry (static definitions, like Phase 12)
DEFAULT_ADAPTER_REGISTRY = AdapterRegistry(_builtin_adapters())
