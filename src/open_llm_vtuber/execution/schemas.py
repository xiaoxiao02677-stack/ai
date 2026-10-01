"""ExecutionResult: outcome of a sandbox-simulated action intent.

Phase 11 domain schema — the eighth and FINAL layer. An ExecutionResult
records what happened when an ActionIntent entered the SANDBOX
executor: SIMULATED (deterministic simulation ran), REJECTED (validation
gate refused the intent), or FAILED (an unexpected internal error —
never masquerading as SIMULATED).

`execution_mode` has exactly one value in this phase: SANDBOX. Real /
live / device modes do not exist in the vocabulary at all — the enum
is a single-element tuple by design.

Provenance: the full intent chain is carried (action/decision/
evaluation/strategy ids) and must match the ActionIntent exactly —
any mismatch is REJECTED before simulation.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List

# the ONLY execution mode that exists in Phase 11 — structurally
# there is no REAL/LIVE/DEVICE mode to accidentally select
EXECUTION_MODE_SANDBOX = "SANDBOX"
EXECUTION_MODES = (EXECUTION_MODE_SANDBOX,)

STATUS_SIMULATED = "SIMULATED"
STATUS_REJECTED = "REJECTED"
STATUS_FAILED = "FAILED"
EXECUTION_STATUSES = (STATUS_SIMULATED, STATUS_REJECTED, STATUS_FAILED)

# reason language boundary (consistent with the whole chain)
_FORBIDDEN_MARKERS = (
    "必须", "务必", "一定要", "强制", "策略",
    "must ", "always ", "never ", "policy", "strategy",
)


@dataclass
class ExecutionResult:
    execution_id: str
    conf_uid: str
    action_id: str = ""
    decision_id: str = ""
    evaluation_id: str = ""
    strategy_id: str = ""
    status: str = STATUS_REJECTED
    execution_mode: str = EXECUTION_MODE_SANDBOX
    result: Dict[str, Any] = field(default_factory=dict)  # simulation payload
    reason: str = ""
    evidence: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    # -- constructors -----------------------------------------------------------

    @staticmethod
    def new(conf_uid: str, action_id: str = "",
            status: str = STATUS_REJECTED) -> "ExecutionResult":
        now = time.time()
        return ExecutionResult(
            execution_id=uuid.uuid4().hex[:16],
            conf_uid=conf_uid,
            action_id=action_id,
            status=status if status in EXECUTION_STATUSES else STATUS_REJECTED,
            execution_mode=EXECUTION_MODE_SANDBOX,  # the only mode
            created_at=now,
            updated_at=now,
        )

    # -- validation ----------------------------------------------------------------

    def validate(self) -> None:
        """Raise ValueError when the record violates Phase-11 boundaries."""
        if not self.execution_id or not self.conf_uid:
            raise ValueError("execution result needs execution_id and conf_uid")
        if self.status not in EXECUTION_STATUSES:
            raise ValueError(
                f"status must be one of {EXECUTION_STATUSES}, got '{self.status}'")
        if self.execution_mode not in EXECUTION_MODES:
            raise ValueError(
                f"execution_mode must be {EXECUTION_MODES} — no other mode "
                f"exists in Phase 11 (got '{self.execution_mode}')")
        if self.status == STATUS_SIMULATED:
            if not self.action_id:
                raise ValueError(
                    "SIMULATED results require action_id (provenance to the "
                    "executed intent)")
            if not self.result.get("simulated") is True:
                raise ValueError(
                    "SIMULATED results must carry result.simulated=true "
                    "(deterministic simulation payload)")
        if self.reason:
            low = self.reason.lower()
            for marker in _FORBIDDEN_MARKERS:
                if marker in low:
                    raise ValueError(
                        f"reason contains commanding marker '{marker}'")

    # -- serialization -----------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "conf_uid": self.conf_uid,
            "action_id": self.action_id,
            "decision_id": self.decision_id,
            "evaluation_id": self.evaluation_id,
            "strategy_id": self.strategy_id,
            "status": self.status,
            "execution_mode": self.execution_mode,
            "result": dict(self.result),
            "reason": self.reason,
            "evidence": list(self.evidence),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ExecutionResult":
        return ExecutionResult(
            execution_id=str(d.get("execution_id", "")),
            conf_uid=str(d.get("conf_uid", "")),
            action_id=str(d.get("action_id", "")),
            decision_id=str(d.get("decision_id", "")),
            evaluation_id=str(d.get("evaluation_id", "")),
            strategy_id=str(d.get("strategy_id", "")),
            status=str(d.get("status", STATUS_REJECTED)),
            execution_mode=str(d.get("execution_mode", EXECUTION_MODE_SANDBOX)),
            result=dict(d.get("result") or {}),
            reason=str(d.get("reason", "")),
            evidence=list(d.get("evidence") or []),
            metadata=dict(d.get("metadata") or {}),
            created_at=float(d.get("created_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
        )

    def touch(self) -> None:
        self.updated_at = time.time()
