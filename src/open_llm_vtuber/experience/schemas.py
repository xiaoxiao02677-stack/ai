"""ExperienceRecord: one concrete interaction that actually happened.

Phase 4 domain schema. An Experience is NOT a Memory: Memory describes
durable facts/preferences/relations ("用户喜欢猫"), an Experience
describes a specific interaction episode ("用户说累了，AI 安慰了他，
用户回复好多了"). This record captures only what the system can
deterministically observe from the conversation lifecycle — no LLM
extraction, no invented fields.

Field policy (per the Phase-4 spec §六):
- Only fields the current pipeline can reliably produce.
- user_input / ai_response pass through the LTM privacy sanitizer
  (truncation + sensitive-content masking) at capture time.
- tool_calls mirrors the tool_call_status events the agent stream
  already emits: [{"tool_name", "status"}] — no payloads, no secrets.
- No user_response/feedback field: the next user turn belongs to
  another Experience; recording it here would be fabrication.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# what the conversation layer can actually tell us about how a turn ended
OUTCOME_TYPES = (
    "turn_complete",     # normal full turn: input -> AI reply -> finalize
    "ai_error",          # agent stream raised; ai_response may be partial
    "empty_reply",       # agent produced no textual response
)

INTERACTION_TYPES = ("chat", "proactive")  # normal turn vs proactive speak


@dataclass
class ExperienceRecord:
    experience_id: str
    conf_uid: str
    history_uid: str = ""
    interaction_type: str = "chat"          # chat | proactive
    user_input: str = ""                    # sanitized user text ("" for audio-only turns that failed ASR)
    ai_response: str = ""                   # sanitized accumulated AI text
    tool_calls: List[Dict[str, str]] = field(default_factory=list)
    outcome: str = ""                       # factual outcome description
    outcome_type: str = "turn_complete"     # OUTCOME_TYPES
    metadata: Dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0                 # epoch, user input processing start
    finalized_at: float = 0.0               # epoch, finalize() call
    created_at: float = 0.0
    updated_at: float = 0.0

    # -- constructors ---------------------------------------------------------

    @staticmethod
    def new(conf_uid: str, history_uid: str = "",
            interaction_type: str = "chat") -> "ExperienceRecord":
        now = time.time()
        return ExperienceRecord(
            experience_id=uuid.uuid4().hex[:16],
            conf_uid=conf_uid,
            history_uid=history_uid,
            interaction_type=interaction_type if interaction_type in INTERACTION_TYPES else "chat",
            started_at=now,
            created_at=now,
            updated_at=now,
        )

    # -- serialization ----------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "conf_uid": self.conf_uid,
            "history_uid": self.history_uid,
            "interaction_type": self.interaction_type,
            "user_input": self.user_input,
            "ai_response": self.ai_response,
            "tool_calls": list(self.tool_calls),
            "outcome": self.outcome,
            "outcome_type": self.outcome_type,
            "metadata": dict(self.metadata),
            "started_at": self.started_at,
            "finalized_at": self.finalized_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ExperienceRecord":
        return ExperienceRecord(
            experience_id=str(d.get("experience_id", "")),
            conf_uid=str(d.get("conf_uid", "")),
            history_uid=str(d.get("history_uid", "")),
            interaction_type=str(d.get("interaction_type", "chat")),
            user_input=str(d.get("user_input", "")),
            ai_response=str(d.get("ai_response", "")),
            tool_calls=list(d.get("tool_calls") or []),
            outcome=str(d.get("outcome", "")),
            outcome_type=str(d.get("outcome_type", "turn_complete")),
            metadata=dict(d.get("metadata") or {}),
            started_at=float(d.get("started_at", 0.0)),
            finalized_at=float(d.get("finalized_at", 0.0)),
            created_at=float(d.get("created_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
        )

    # -- lifecycle helpers --------------------------------------------------------

    def touch(self) -> None:
        self.updated_at = time.time()

    @property
    def is_finalized(self) -> bool:
        return self.finalized_at > 0.0
