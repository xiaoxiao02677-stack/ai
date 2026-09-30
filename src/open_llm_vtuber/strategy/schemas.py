"""StrategyRecord: a reusable situation→action guideline from lessons.

Phase 7 domain schema. A Strategy is the third distillation layer:
Experience captures what happened, Reflection states what the data
shows, Lesson states a reusable takeaway — a Strategy binds a
*condition* (when this situation arises) to a *recommendation* (this
approach tends to work). Strategy wording IS advisory by nature
("建议/通常做法/倾向") but hard commanding language (必须/务必/
一定要/强制 — and even the word 策略 itself as instruction phrasing)
is rejected: a strategy GUIDES, it never commands, and nothing in
this phase feeds it into prompts or behavior automatically.

Traceability: ``source_lesson_ids`` + ``evidence`` link every strategy
back to the lessons (and through them, reflections/experiences) it
was derived from.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# hard commanding markers — rejected at validation
_FORBIDDEN_MARKERS = (
    "必须", "务必", "一定要", "强制", "策略",
    "must ", "always ", "never ", "policy", "strategy",
)


@dataclass
class StrategyRecord:
    strategy_id: str
    conf_uid: str
    source_lesson_ids: List[str] = field(default_factory=list)
    condition: str = ""                     # situation trigger description
    recommendation: str = ""                # advisory action guideline
    evidence: List[str] = field(default_factory=list)
    confidence: float = 0.5                 # 0.0~1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    # -- constructors -----------------------------------------------------------

    @staticmethod
    def new(conf_uid: str,
            source_lesson_ids: Optional[List[str]] = None) -> "StrategyRecord":
        now = time.time()
        return StrategyRecord(
            strategy_id=uuid.uuid4().hex[:16],
            conf_uid=conf_uid,
            source_lesson_ids=list(source_lesson_ids or []),
            created_at=now,
            updated_at=now,
        )

    # -- validation ----------------------------------------------------------------

    def validate(self) -> None:
        """Raise ValueError when the record violates Phase-7 boundaries."""
        if not self.strategy_id or not self.conf_uid:
            raise ValueError("strategy needs strategy_id and conf_uid")
        if not self.condition or not self.condition.strip():
            raise ValueError("strategy needs a condition (when it applies)")
        if not self.recommendation or not self.recommendation.strip():
            raise ValueError("strategy needs a recommendation (advisory action)")
        for text in (self.condition, self.recommendation):
            low = text.lower()
            for marker in _FORBIDDEN_MARKERS:
                if marker in low:
                    raise ValueError(
                        f"strategy contains hard-commanding marker "
                        f"'{marker}' — Phase 7 strategies guide, never command")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be within [0.0, 1.0]")
        # traceability: a strategy without lesson sources is unfounded
        if not self.source_lesson_ids:
            raise ValueError("strategy requires source_lesson_ids")
        if self.evidence and not set(self.evidence) <= set(self.source_lesson_ids):
            raise ValueError("evidence ids must come from source_lesson_ids")

    # -- serialization -----------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "conf_uid": self.conf_uid,
            "source_lesson_ids": list(self.source_lesson_ids),
            "condition": self.condition,
            "recommendation": self.recommendation,
            "evidence": list(self.evidence),
            "confidence": self.confidence,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "StrategyRecord":
        return StrategyRecord(
            strategy_id=str(d.get("strategy_id", "")),
            conf_uid=str(d.get("conf_uid", "")),
            source_lesson_ids=list(d.get("source_lesson_ids") or []),
            condition=str(d.get("condition", "")),
            recommendation=str(d.get("recommendation", "")),
            evidence=list(d.get("evidence") or []),
            confidence=float(d.get("confidence", 0.5)),
            metadata=dict(d.get("metadata") or {}),
            created_at=float(d.get("created_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
        )

    def touch(self) -> None:
        self.updated_at = time.time()
