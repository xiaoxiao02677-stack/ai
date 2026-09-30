"""EvaluationRecord: applicability judgment of a Strategy for a context.

Phase 8 domain schema. An Evaluation answers ONE question: "how
relevant is THIS strategy to THIS context, right now?" It is a
judgment (scores + reason), never an action — nothing downstream in
this phase executes, injects, or mutates anything based on it.

``strategy_id`` is mandatory provenance: every evaluation must trace
back to the strategy it judged (and through it, lesson → reflection →
experience). Scores are bounded floats; ``reason`` is an explanation,
phrased as observation ("当前上下文出现焦虑表达"), never a command.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# evaluation reasons describe matches, they never command — the same
# boundary markers as the strategy domain (which also forbids 策略)
_FORBIDDEN_MARKERS = (
    "必须", "务必", "一定要", "强制", "策略",
    "must ", "always ", "never ", "policy", "strategy",
)


@dataclass
class EvaluationRecord:
    evaluation_id: str
    conf_uid: str
    strategy_id: str = ""                 # judged strategy (provenance)
    applicable: bool = False
    relevance: float = 0.0                # 0.0~1.0 context↔strategy fit
    confidence: float = 0.0               # 0.0~1.0 judgment certainty
    condition_match: float = 0.0          # 0.0~1.0 keyword-overlap score
    reason: str = ""                      # factual explanation
    evidence: List[str] = field(default_factory=list)  # matched keywords
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    # -- constructors -----------------------------------------------------------

    @staticmethod
    def new(conf_uid: str, strategy_id: str) -> "EvaluationRecord":
        now = time.time()
        return EvaluationRecord(
            evaluation_id=uuid.uuid4().hex[:16],
            conf_uid=conf_uid,
            strategy_id=strategy_id,
            created_at=now,
            updated_at=now,
        )

    # -- validation -----------------------------------------------------------------

    def validate(self) -> None:
        """Raise ValueError when the record violates Phase-8 boundaries."""
        if not self.evaluation_id or not self.conf_uid:
            raise ValueError("evaluation needs evaluation_id and conf_uid")
        if not self.strategy_id:
            raise ValueError(
                "evaluation requires strategy_id (provenance to the "
                "judged strategy — source-less evaluations are rejected)")
        for name, val in (("relevance", self.relevance),
                          ("confidence", self.confidence),
                          ("condition_match", self.condition_match)):
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"{name} must be within [0.0, 1.0]")
        if self.reason:
            low = self.reason.lower()
            for marker in _FORBIDDEN_MARKERS:
                if marker in low:
                    raise ValueError(
                        f"reason contains commanding marker '{marker}' — "
                        f"evaluations describe, never command")

    # -- serialization -----------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "conf_uid": self.conf_uid,
            "strategy_id": self.strategy_id,
            "applicable": self.applicable,
            "relevance": self.relevance,
            "confidence": self.confidence,
            "condition_match": self.condition_match,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "EvaluationRecord":
        return EvaluationRecord(
            evaluation_id=str(d.get("evaluation_id", "")),
            conf_uid=str(d.get("conf_uid", "")),
            strategy_id=str(d.get("strategy_id", "")),
            applicable=bool(d.get("applicable", False)),
            relevance=float(d.get("relevance", 0.0)),
            confidence=float(d.get("confidence", 0.0)),
            condition_match=float(d.get("condition_match", 0.0)),
            reason=str(d.get("reason", "")),
            evidence=list(d.get("evidence") or []),
            metadata=dict(d.get("metadata") or {}),
            created_at=float(d.get("created_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
        )

    def touch(self) -> None:
        self.updated_at = time.time()
