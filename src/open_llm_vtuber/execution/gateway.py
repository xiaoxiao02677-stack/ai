"""ExecutionGateway: the single entry point for ALL execution backends.

Phase 13 establishes the boundary every future backend (ESP32, MQTT,
MCP, HTTP, TTS, notifications, ...) MUST go through — none may be
reached directly. The gateway composes the existing chain and adds
the authorization layer:

    ActionIntent (fetched, provenance verified against stored records)
      -> CapabilityResolver (deterministic, from Phase 12)
      -> CapabilityContract
      -> AdapterRegistry lookup (static, frozen)
      -> ExecutionPolicy (default deny; global kill switch)
      -> ExecutionRequest (frozen snapshot — the intent itself is
         never mutated)
      -> adapter.run (fake/PURE in Phase 13)
      -> ExecutionResult (persisted via the repository)

The gateway does NOT re-decide, re-evaluate or re-rank anything; it
rejects anything with broken provenance and never falls back to
another adapter/capability. The Phase-11/12 SandboxExecutor path
remains fully available and untouched (execute_action_sandbox) —
the gateway is the forward-looking composition, not a rename.
"""

from typing import Optional

from loguru import logger

from ..action.repository import ActionRepository
from ..action.schemas import ActionIntentRecord
from ..capability import get_resolver
from ..capability.resolver import CapabilityResolver
from ..capability.schemas import CapabilityContract, RESOLVED as CAP_RESOLVED
from ..decision.repository import DecisionRepository
from ..decision.schemas import STATUS_SELECTED
from ..evaluation.repository import EvaluationRepository
from .adapter import (AdapterRegistry, DEFAULT_ADAPTER_REGISTRY,
                      ExecutionRequest)
from .policy import (ExecutionPolicy, PolicyDecision, POLICY_SANDBOX)
from .repository import ExecutionRepository
from .schemas import ExecutionResult, STATUS_SIMULATED, STATUS_REJECTED, STATUS_EXECUTED


class ExecutionGateway:
    """Single, auditable, blockable execution boundary."""

    def __init__(self, execution_repo: ExecutionRepository,
                 action_repo: ActionRepository,
                 decision_repo: DecisionRepository,
                 evaluation_repo: EvaluationRepository,
                 resolver: Optional[CapabilityResolver] = None,
                 adapter_registry: Optional[AdapterRegistry] = None,
                 policy: Optional[ExecutionPolicy] = None,
                 config: Optional[dict] = None):
        self.execution_repo = execution_repo
        self.action_repo = action_repo
        self.decision_repo = decision_repo
        self.evaluation_repo = evaluation_repo
        self.resolver = resolver if resolver is not None else get_resolver()
        self.adapters = (adapter_registry if adapter_registry is not None
                         else DEFAULT_ADAPTER_REGISTRY)
        self.policy = policy if policy is not None else ExecutionPolicy()
        self.config = config or {}

    # -- public API ------------------------------------------------------------

    def execute(self, action_id: str, conf_uid: str) -> Optional[ExecutionResult]:
        """Route one intent through the full boundary. Returns the persisted
        ExecutionResult, or None only when the intent id is unknown."""
        intent = self.action_repo.get(action_id)
        if intent is None or intent.conf_uid != conf_uid:
            logger.warning(
                f"[GWY] action intent '{action_id}' not found for {conf_uid}")
            return None

        # 1. provenance against the STORED chain (same discipline as P11)
        chain_error = self._verify_chain(intent, conf_uid)
        if chain_error:
            return self._rejected(intent, conf_uid,
                                  f"provenance verification failed: {chain_error}")

        # 2. capability resolution (deterministic, Phase 12)
        resolution = self.resolver.resolve(intent.action_type)
        if resolution.status != CAP_RESOLVED or resolution.capability is None:
            return self._rejected(
                intent, conf_uid,
                f"capability resolution failed ({resolution.status}): "
                f"{resolution.reason}")
        contract = resolution.capability

        # 3. adapter lookup (static registry; unknown -> reject, no fallback)
        adapter = self.adapters.get(contract.capability_id)
        if adapter is None:
            return self._rejected(
                intent, conf_uid,
                f"no adapter registered for capability "
                f"'{contract.capability_id}' (no fallback)")

        # 4. contract input validation (closed schema, Phase 12 authority)
        schema_error = contract.validate_input(intent.parameters)
        if schema_error:
            return self._rejected(
                intent, conf_uid,
                f"contract input validation failed: {schema_error}")

        # 5. authorization (default deny; kill switch). P20: non-PURE
        # adapters ask the conf-aware p20 path (grant table + switches);
        # PURE keeps the sandbox decision. Both default deny.
        if adapter.side_effect_level == "PURE":
            decision = self.policy.decide(contract, adapter)
        else:
            decision = self.policy.decide_p20(contract, adapter, conf_uid)
        if decision.status not in (POLICY_SANDBOX, "REAL_ALLOWED"):
            return self._rejected(
                intent, conf_uid,
                f"execution policy denied: {decision.reason}")

        # 6. frozen request + adapter run (intent never mutated)
        request = ExecutionRequest(
            conf_uid=conf_uid, intent=intent, contract=contract,
            policy_status=decision.status, adapter_id=adapter.adapter_id)
        try:
            payload = adapter.run(request)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[GWY] adapter '{adapter.adapter_id}' raised: {e}")
            return self._failed(intent, conf_uid, adapter,
                                f"adapter exception: {e}")

        # 7. result assembly + persistence
        if payload.get("status") == "DEVICE_RESULT":
            # P20 real-device path: the adapter talked to a REAL device
            # and holds a validated DeviceAck. Require the full evidence
            # set before accepting EXECUTED (never trusted blind).
            if not (payload.get("device_ack") is True
                    and payload.get("command_id")
                    and payload.get("device_id")):
                return self._failed(
                    intent, conf_uid, adapter,
                    "device payload missing ack evidence")
            rec = ExecutionResult.new(conf_uid, intent.action_id,
                                      STATUS_EXECUTED)
        elif payload.get("simulated") is True:
            rec = ExecutionResult.new(conf_uid, intent.action_id,
                                      STATUS_SIMULATED)
        elif payload.get("status") in ("DEVICE_FAILED", "DEVICE_TIMEOUT",
                                       "DEVICE_GATE_REJECTED"):
            # P20: the adapter reached the device boundary and the
            # DEVICE answered negatively (NACK / timeout / gate). This
            # is an honest FAILED outcome with the device evidence kept.
            return self._failed(
                intent, conf_uid, adapter,
                f"device outcome: {payload.get('status')} "
                f"({payload.get('reason') or payload.get('error_code')})")
        else:
            # anything else is an internal failure (never trusted blind)
            return self._failed(intent, conf_uid, adapter,
                                "adapter payload missing simulated=true")
        rec.decision_id = intent.decision_id
        rec.evaluation_id = intent.evaluation_id
        rec.strategy_id = intent.strategy_id
        rec.result = dict(payload)
        rec.reason = (f"gateway 执行完成（授权={decision.status}, "
                      f"适配器={adapter.adapter_id}, "
                      f"能力={contract.capability_id}）")
        rec.evidence = [intent.action_id]
        rec.metadata = {"gateway": True, "adapter_id": adapter.adapter_id,
                        "capability_id": contract.capability_id,
                        "side_effect_level": adapter.side_effect_level,
                        "policy_status": decision.status}
        try:
            self.execution_repo.save(rec)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[GWY] execution result save failed: {e}")
        return rec

    # -- internals ----------------------------------------------------------------

    def _rejected(self, intent: ActionIntentRecord, conf_uid: str,
                  reason: str) -> ExecutionResult:
        rec = ExecutionResult.new(conf_uid, intent.action_id,
                                  STATUS_REJECTED)
        rec.decision_id = intent.decision_id
        rec.evaluation_id = intent.evaluation_id
        rec.strategy_id = intent.strategy_id
        rec.reason = reason
        rec.metadata = {"gateway": True}
        try:
            self.execution_repo.save(rec)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[GWY] rejected-result save failed: {e}")
        return rec

    def _failed(self, intent: ActionIntentRecord, conf_uid: str,
                 adapter, reason: str) -> ExecutionResult:
        rec = ExecutionResult.new(conf_uid, intent.action_id, "FAILED")
        rec.decision_id = intent.decision_id
        rec.evaluation_id = intent.evaluation_id
        rec.strategy_id = intent.strategy_id
        rec.reason = reason
        rec.metadata = {"gateway": True,
                        "adapter_id": getattr(adapter, "adapter_id", "")}
        try:
            self.execution_repo.save(rec)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[GWY] failed-result save failed: {e}")
        return rec

    def _verify_chain(self, intent: ActionIntentRecord,
                      conf_uid: str) -> Optional[str]:
        """Stored-chain provenance check (same rules as Phase 11)."""
        if intent.status != "planned":
            return f"intent status is '{intent.status}' (not planned)"
        decision = self.decision_repo.get(intent.decision_id)
        if decision is None or decision.conf_uid != conf_uid:
            return f"decision '{intent.decision_id}' not found in this conf"
        if decision.status != STATUS_SELECTED:
            return f"decision status is '{decision.status}' (not selected)"
        if decision.selected_evaluation_id != intent.evaluation_id:
            return ("intent.evaluation_id != decision.selected_evaluation_id")
        if decision.selected_strategy_id != intent.strategy_id:
            return ("intent.strategy_id != decision.selected_strategy_id")
        evaluation = self.evaluation_repo.get(intent.evaluation_id)
        if evaluation is None or evaluation.conf_uid != conf_uid:
            return f"evaluation '{intent.evaluation_id}' not found in this conf"
        if evaluation.strategy_id != intent.strategy_id:
            return "evaluation.strategy_id != intent.strategy_id"
        return None
