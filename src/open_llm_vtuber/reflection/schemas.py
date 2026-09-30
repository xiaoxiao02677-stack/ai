"""ReflectionRecord: a high-level observation derived from Experience data.

Phase 5 domain schema. A Reflection is NOT a Lesson and NOT a Strategy:
it describes *what the data shows* across a set of experiences ("过去
7 次互动中用户表达疲惫后 6 次继续对话"), never *what to do next*
("以后应主动安慰" is forbidden). The `observation` field is fact-only,
`evidence` / `source_experience_ids` keep every claim traceable to the
ExperienceRecords it was computed from.

Time representation follows the Experience domain convention: epoch
seconds (float), not ISO strings — one convention per codebase.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# analyzer-emitted types (deterministic rule kinds + open LLM kinds)
REFLECTION_TYPES = (
    "interaction_pattern",     # how user/AI turns relate across episodes
    "frequency_analysis",      # how often something occurs
    "outcome_distribution",    # how turns ended (turn_complete/ai_error/…)
    "tool_usage",              # tool-call regularities
)

# forbidden phrases: an observation containing these is a lesson/strategy,
# not a reflection — rejected at validation time
_FORBIDDEN_MARKERS = (
    "应该", "应当", "建议", "下次", "以后要", "需要主动", "策略",
    "lesson", "strategy", "should ", "next time", "we recommend",
)


@dataclass
class ReflectionRecord:
    reflection_id: str
    conf_uid: str
    source_experience_ids: List[str] = field(default_factory=list)
    time_window_start: float = 0.0     # epoch; 0.0 = unbounded
    time_window_end: float = 0.0
    reflection_type: str = "interaction_pattern"
    observation: str = ""              # fact-only, evidence-backed
    evidence: List[str] = field(default_factory=list)
    confidence: float = 0.5            # 0.0~1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    # -- constructors --------------------------------------------------------

    @staticmethod
    def new(conf_uid: str, reflection_type: str = "interaction_pattern",
            source_experience_ids: Optional[List[str]] = None) -> "ReflectionRecord":
        now = time.time()
        return ReflectionRecord(
            reflection_id=uuid.uuid4().hex[:16],
            conf_uid=conf_uid,
            source_experience_ids=list(source_experience_ids or []),
            reflection_type=reflection_type,
            created_at=now,
            updated_at=now,
        )

    # -- validation ------------------------------------------------------------

    def validate(self) -> None:
        """Raise ValueError when the record violates Phase-5 boundaries."""
        if not self.reflection_id or not self.conf_uid:
            raise ValueError("reflection needs reflection_id and conf_uid")
        if not self.observation or not self.observation.strip():
            raise ValueError("observation must be a non-empty fact")
        low = self.observation.lower()
        for marker in _FORBIDDEN_MARKERS:
            if marker in low:
                raise ValueError(
                    f"observation contains a lesson/strategy marker "
                    f"'{marker}' — Phase 5 reflections are fact-only")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be within [0.0, 1.0]")
        # every claim must be traceable: no evidence without sources
        if self.evidence and not self.source_experience_ids:
            raise ValueError("evidence requires source_experience_ids")

    # -- serialization -----------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reflection_id": self.reflection_id,
            "conf_uid": self.conf_uid,
            "source_experience_ids": list(self.source_experience_ids),
            "time_window_start": self.time_window_start,
            "time_window_end": self.time_window_end,
            "reflection_type": self.reflection_type,
            "observation": self.observation,
            "evidence": list(self.evidence),
            "confidence": self.confidence,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ReflectionRecord":
        return ReflectionRecord(
            reflection_id=str(d.get("reflection_id", "")),
            conf_uid=str(d.get("conf_uid", "")),
            source_experience_ids=list(d.get("source_experience_ids") or []),
            time_window_start=float(d.get("time_window_start", 0.0)),
            time_window_end=float(d.get("time_window_end", 0.0)),
            reflection_type=str(d.get("reflection_type", "interaction_pattern")),
            observation=str(d.get("observation", "")),
            evidence=list(d.get("evidence") or []),
            confidence=float(d.get("confidence", 0.5)),
            metadata=dict(d.get("metadata") or {}),
            created_at=float(d.get("created_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
        )

    def touch(self) -> None:
        self.updated_at = time.time()
