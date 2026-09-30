"""LessonRecord: a reusable takeaway distilled from Reflection records.

Phase 6 domain schema. A Lesson is one step further than a Reflection:
the Reflection layer states *what the data shows*; the Lesson layer
states *a reusable takeaway worth remembering* — INCLUDING advisory
wording ("用户疲惫时先共情再给建议的效果更好") but NEVER hard
policy/imperative language ("必须/should/always/下次一定要"). A lesson
informs; it never commands, and nothing in this phase feeds it back
into prompts or behavior automatically.

Traceability: ``source_reflection_ids`` links every lesson back to the
reflection records it was distilled from.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# hard policy/imperative markers — a lesson containing these is a
# command, not a takeaway; rejected at validation
_FORBIDDEN_MARKERS = (
    "必须", "务必", "一定要", "下次必须", "策略", "强制",
    "must ", "always ", "never ", "policy", "strategy",
)

# advisory phrasing is explicitly ALLOWED (unlike Phase 5 reflections):
# 最好/建议/更适合/倾向于/效果更好/先…再… are legitimate lesson wording


@dataclass
class LessonRecord:
    lesson_id: str
    conf_uid: str
    source_reflection_ids: List[str] = field(default_factory=list)
    lesson: str = ""                      # reusable takeaway text
    confidence: float = 0.5               # 0.0~1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    # -- constructors -----------------------------------------------------------

    @staticmethod
    def new(conf_uid: str,
            source_reflection_ids: Optional[List[str]] = None) -> "LessonRecord":
        now = time.time()
        return LessonRecord(
            lesson_id=uuid.uuid4().hex[:16],
            conf_uid=conf_uid,
            source_reflection_ids=list(source_reflection_ids or []),
            created_at=now,
            updated_at=now,
        )

    # -- validation ----------------------------------------------------------------

    def validate(self) -> None:
        """Raise ValueError when the record violates Phase-6 boundaries."""
        if not self.lesson_id or not self.conf_uid:
            raise ValueError("lesson needs lesson_id and conf_uid")
        if not self.lesson or not self.lesson.strip():
            raise ValueError("lesson must be a non-empty takeaway")
        low = self.lesson.lower()
        for marker in _FORBIDDEN_MARKERS:
            if marker in low:
                raise ValueError(
                    f"lesson contains hard-policy marker '{marker}' — "
                    f"Phase 6 lessons inform, they never command")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be within [0.0, 1.0]")
        # traceability: a lesson without sources is an unfounded claim
        if not self.source_reflection_ids:
            raise ValueError("lesson requires source_reflection_ids")

    # -- serialization -----------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lesson_id": self.lesson_id,
            "conf_uid": self.conf_uid,
            "source_reflection_ids": list(self.source_reflection_ids),
            "lesson": self.lesson,
            "confidence": self.confidence,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "LessonRecord":
        return LessonRecord(
            lesson_id=str(d.get("lesson_id", "")),
            conf_uid=str(d.get("conf_uid", "")),
            source_reflection_ids=list(d.get("source_reflection_ids") or []),
            lesson=str(d.get("lesson", "")),
            confidence=float(d.get("confidence", 0.5)),
            metadata=dict(d.get("metadata") or {}),
            created_at=float(d.get("created_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
        )

    def touch(self) -> None:
        self.updated_at = time.time()
