"""DecisionRecord: a structured, explainable choice among evaluations.

Phase 9 domain schema — the LAST layer of the distillation chain. A
Decision consumes Evaluation records (never strategies directly) and
either selects one (status=selected, with full provenance) or refuses
to choose (status=abstain — no applicable candidate, insufficient
confidence, or ambiguous ties). `rejected` is reserved for explicitly
discarding a specific candidate in future multi-decision flows.

A Decision is a RECORD, not a command: nothing downstream executes,
and the chain ends here — Decision → STOP.

Provenance invariants (validated):
- selected decisions MUST carry both selected_evaluation_id and
  selected_strategy_id, and they must agree with each other
  (strategy_id of the chosen evaluation);
- abstain decisions carry neither;
- every number is bounded.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# explicit status enum — no fuzzy strings
STATUS_SELECTED = "selected"
STATUS_ABSTAIN = "abstain"
STATUS_REJECTED = "rejected"   # reserved: explicit per-candidate discard
DECISION_STATUSES = (STATUS_SELECTED, STATUS_ABSTAIN, STATUS_REJECTED)

# reason language: decisions explain, they never command
_FORBIDDEN_MARKERS = (
    "必须", "务必", "一定要", "强制", "策略",
    "must ", "always ", "never ", "policy", "strategy",
)


@dataclass
class DecisionRecord:
    decision_id: str
    conf_uid: str
    status: str = STATUS_ABSTAIN
    selected_strategy_id: str = ""        # "" for abstain
    selected_evaluation_id: str = ""      # "" for abstain
    confidence: float = 0.0               # decision-level certainty
    reason: str = ""                      # why selected / why abstained
    evidence: List[str] = field(default_factory=list)  # candidate evaluation ids considered
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    # -- constructors -----------------------------------------------------------

    @staticmethod
    def new(conf_uid: str, status: str = STATUS_ABSTAIN) -> "DecisionRecord":
        now = time.time()
        return DecisionRecord(
            decision_id=uuid.uuid4().hex[:16],
            conf_uid=conf_uid,
            status=status if status in DECISION_STATUSES else STATUS_ABSTAIN,
            created_at=now,
            updated_at=now,
        )

    # -- validation ----------------------------------------------------------------

    def validate(self) -> None:
        """Raise ValueError when the record violates Phase-9 boundaries."""
        if not self.decision_id or not self.conf_uid:
            raise ValueError("decision needs decision_id and conf_uid")
        if self.status not in DECISION_STATUSES:
            raise ValueError(
                f"status must be one of {DECISION_STATUSES}, got '{self.status}'")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be within [0.0, 1.0]")
        if self.status == STATUS_SELECTED:
            if not self.selected_evaluation_id or not self.selected_strategy_id:
                raise ValueError(
                    "selected decisions require BOTH selected_evaluation_id "
                    "and selected_strategy_id (provenance)")
        else:
            # abstain/rejected carry no selection
            if self.selected_strategy_id or self.selected_evaluation_id:
                raise ValueError(
                    f"{self.status} decisions must not carry a selection")
        if self.reason:
            low = self.reason.lower()
            for marker in _FORBIDDEN_MARKERS:
                if marker in low:
                    raise ValueError(
                        f"reason contains commanding marker '{marker}' — "
                        f"decisions explain, never command")

    # -- serialization -----------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "conf_uid": self.conf_uid,
            "status": self.status,
            "selected_strategy_id": self.selected_strategy_id,
            "selected_evaluation_id": self.selected_evaluation_id,
            "confidence": self.confidence,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "DecisionRecord":
        return DecisionRecord(
            decision_id=str(d.get("decision_id", "")),
            conf_uid=str(d.get("conf_uid", "")),
            status=str(d.get("status", STATUS_ABSTAIN)),
            selected_strategy_id=str(d.get("selected_strategy_id", "")),
            selected_evaluation_id=str(d.get("selected_evaluation_id", "")),
            confidence=float(d.get("confidence", 0.0)),
            reason=str(d.get("reason", "")),
            evidence=list(d.get("evidence") or []),
            metadata=dict(d.get("metadata") or {}),
            created_at=float(d.get("created_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
        )

    def touch(self) -> None:
        self.updated_at = time.time()
