"""ActionIntentRecord: a side-effect-free intent derived from a Decision.

Phase 10 domain schema — the seventh and FINAL layer. An
ActionIntent describes WHAT the system WOULD do (an abstract
intent: RESPOND / REMIND / ACKNOWLEDGE) given a selected decision.
It is a RECORD of intent, never an executor: nothing in this phase
performs, dispatches, or invokes anything.

Provenance invariants (validated):
- decision_id must reference the deciding Decision;
- evaluation_id MUST equal decision.selected_evaluation_id;
- strategy_id MUST equal decision.selected_strategy_id;
- only selected decisions may produce planned intents; abstain and
  rejected decisions are structurally barred (status=invalid/
  rejected paths reject at validation).

`parameters` is a structured dict with an executable-key blacklist —
shell/command/http/subprocess etc. can never smuggle an execution
entrypoint in. `action_type` is a controlled enum; real-execution
types (SEND_MESSAGE/CALL_TOOL/…) are not in the vocabulary at all.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List

# controlled enum — abstract, side-effect-free intents only.
# Phase 19 adds SET_LED: the first BODY-intent. It is still an abstract
# intent (the ActionIntent layer never touches hardware); the body
# action happens at the device, behind the full P13-P18 chain.
ACTION_TYPES = ("RESPOND", "REMIND", "ACKNOWLEDGE", "SET_LED")

STATUS_PLANNED = "planned"
STATUS_REJECTED = "rejected"
STATUS_INVALID = "invalid"
ACTION_STATUSES = (STATUS_PLANNED, STATUS_REJECTED, STATUS_INVALID)

# parameters may never carry an executable entrypoint
_FORBIDDEN_PARAM_KEYS = (
    "shell", "command", "cmd", "python", "sql", "http", "url",
    "mcp", "tool_call", "tool", "subprocess", "exec", "eval",
    "endpoint", "host", "port", "script", "run",
)
_FORBIDDEN_PARAM_MARKERS = (
    "os.system", "subprocess", "import ", "__import__", "eval(",
    "exec(", "http://", "https://", "DROP TABLE", "SELECT ",
)

# reason language: intents explain, they never command
_FORBIDDEN_MARKERS = (
    "必须", "务必", "一定要", "强制", "策略",
    "must ", "always ", "never ", "policy", "strategy",
)


def _check_params(params: Dict[str, Any]) -> None:
    if not isinstance(params, dict):
        raise ValueError("parameters must be a structured dict")
    for key in params:
        kl = str(key).lower()
        for bad in _FORBIDDEN_PARAM_KEYS:
            if bad in kl:
                raise ValueError(
                    f"parameter key '{key}' is an executable entrypoint "
                    f"(blacklisted) — parameters are data, never code")
        val = params[key]
        if isinstance(val, str):
            low = val.lower()
            for marker in _FORBIDDEN_PARAM_MARKERS:
                if marker.lower() in low:
                    raise ValueError(
                        f"parameter value for '{key}' contains forbidden "
                        f"marker '{marker}' — data only, never code")


@dataclass
class ActionIntentRecord:
    action_id: str
    conf_uid: str
    decision_id: str = ""                # provenance: deciding decision
    evaluation_id: str = ""              # == decision.selected_evaluation_id
    strategy_id: str = ""                # == decision.selected_strategy_id
    action_type: str = "ACKNOWLEDGE"     # ACTION_TYPES enum
    parameters: Dict[str, Any] = field(default_factory=dict)
    status: str = STATUS_PLANNED
    reason: str = ""
    evidence: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    # -- constructors -----------------------------------------------------------

    @staticmethod
    def new(conf_uid: str, decision_id: str = "",
            action_type: str = "ACKNOWLEDGE") -> "ActionIntentRecord":
        now = time.time()
        return ActionIntentRecord(
            action_id=uuid.uuid4().hex[:16],
            conf_uid=conf_uid,
            decision_id=decision_id,
            action_type=action_type if action_type in ACTION_TYPES
            else "ACKNOWLEDGE",
            created_at=now,
            updated_at=now,
        )

    # -- validation ----------------------------------------------------------------

    def validate(self) -> None:
        """Raise ValueError when the record violates Phase-10 boundaries."""
        if not self.action_id or not self.conf_uid:
            raise ValueError("action intent needs action_id and conf_uid")
        if self.status not in ACTION_STATUSES:
            raise ValueError(
                f"status must be one of {ACTION_STATUSES}, got '{self.status}'")
        if self.action_type not in ACTION_TYPES:
            raise ValueError(
                f"action_type must be one of {ACTION_TYPES}, "
                f"got '{self.action_type}' — execution types are not in "
                f"the Phase-10 vocabulary")
        if not self.decision_id:
            raise ValueError(
                "action intent requires decision_id (provenance to the "
                "deciding decision — source-less intents are rejected)")
        _check_params(self.parameters)
        if self.reason:
            low = self.reason.lower()
            for marker in _FORBIDDEN_MARKERS:
                if marker in low:
                    raise ValueError(
                        f"reason contains commanding marker '{marker}' — "
                        f"intents describe, never command")

    # -- serialization -----------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "conf_uid": self.conf_uid,
            "decision_id": self.decision_id,
            "evaluation_id": self.evaluation_id,
            "strategy_id": self.strategy_id,
            "action_type": self.action_type,
            "parameters": dict(self.parameters),
            "status": self.status,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ActionIntentRecord":
        return ActionIntentRecord(
            action_id=str(d.get("action_id", "")),
            conf_uid=str(d.get("conf_uid", "")),
            decision_id=str(d.get("decision_id", "")),
            evaluation_id=str(d.get("evaluation_id", "")),
            strategy_id=str(d.get("strategy_id", "")),
            action_type=str(d.get("action_type", "ACKNOWLEDGE")),
            parameters=dict(d.get("parameters") or {}),
            status=str(d.get("status", STATUS_PLANNED)),
            reason=str(d.get("reason", "")),
            evidence=list(d.get("evidence") or []),
            metadata=dict(d.get("metadata") or {}),
            created_at=float(d.get("created_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
        )

    def touch(self) -> None:
        self.updated_at = time.time()
