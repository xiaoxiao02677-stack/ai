"""ExperienceEngine: capture lifecycle for one conversation turn.

Phase 4 deterministic capture — constructs an ExperienceRecord purely
from structures the conversation layer already produces (user input,
tool_call_status events, accumulated AI text, turn outcome). No LLM
call, no embedding, no analysis: an engine instance is a tiny state
machine, not a second agent.

Lifecycle (mirrors the single_conversation turn):

    engine = ExperienceEngine.start(conf_uid, history_uid, interaction_type)
    engine.record_user_input(text)          # after ASR / text input
    engine.record_tool(name, status)        # per tool_call_status event
    engine.record_ai_response(full_text)    # after the agent stream ends
    record = engine.finalize(outcome_type)  # stamps outcome + finalized_at

finalize() is idempotent-ish: calling it twice keeps the first
finalization time; record_ai_response after finalize is refused (the
episode is closed). Persistence is the conversation layer's call
(``record_turn`` in the package facade does engine + repository +
background save in one shot).

This engine records facts only — no lessons, strategies or rules.
Those belong to future phases and must not appear here.
"""

import time
from typing import Optional

from .schemas import ExperienceRecord, OUTCOME_TYPES


class ExperienceEngine:
    """Stateful recorder for a single interaction episode."""

    def __init__(self, record: ExperienceRecord):
        self._record = record

    # -- lifecycle ----------------------------------------------------------------

    @staticmethod
    def start(conf_uid: str, history_uid: str = "",
              interaction_type: str = "chat") -> "ExperienceEngine":
        return ExperienceEngine(
            ExperienceRecord.new(conf_uid, history_uid, interaction_type))

    @property
    def record(self) -> ExperienceRecord:
        return self._record

    def record_user_input(self, text: str) -> None:
        if self._record.is_finalized:
            return
        self._record.user_input = text or ""
        self._record.touch()

    def record_tool(self, tool_name: str, status: str) -> None:
        """Log one tool_call_status event (name + status only, no payloads)."""
        if self._record.is_finalized:
            return
        self._record.tool_calls.append(
            {"tool_name": str(tool_name or "unknown"), "status": str(status or "unknown")})
        self._record.touch()

    def record_ai_response(self, full_text: str) -> None:
        if self._record.is_finalized:
            return
        self._record.ai_response = full_text or ""
        self._record.touch()

    def record_outcome(self, outcome: str) -> None:
        """Factual outcome note ('用户继续对话'); never a lesson/strategy."""
        if self._record.is_finalized:
            return
        self._record.outcome = outcome or ""

    def finalize(self, outcome_type: str = "turn_complete") -> ExperienceRecord:
        """Close the episode. Returns the record for persistence."""
        if not self._record.is_finalized:
            self._record.outcome_type = (
                outcome_type if outcome_type in OUTCOME_TYPES else "turn_complete")
            self._record.finalized_at = time.time()
            self._record.touch()
        return self._record
