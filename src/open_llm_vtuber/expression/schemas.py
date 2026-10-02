"""P23 Expression domain objects: state model + intent + policy."""

import time
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# closed expression-state model (system-event driven; NOT emotions)
# ---------------------------------------------------------------------------
EXPRESSION_STATES = (
    "IDLE",        # no activity
    "THINKING",    # decision/evaluation in progress
    "EXECUTING",   # an action is being dispatched to the device
    "SUCCESS",     # last action ACKED
    "ERROR",       # last action failed (NACK/TIMEOUT/FAILED/rejected)
    "WAITING",     # waiting for user input
)

# allowed transitions (state machine; idempotent self-transitions are
# allowed as refreshes)
_EXPRESSION_TRANSITIONS = {
    "IDLE": {"IDLE", "THINKING", "WAITING"},
    "THINKING": {"THINKING", "EXECUTING", "WAITING", "IDLE", "ERROR"},
    "EXECUTING": {"EXECUTING", "SUCCESS", "ERROR", "IDLE"},
    "SUCCESS": {"SUCCESS", "IDLE", "THINKING", "WAITING"},
    "ERROR": {"ERROR", "IDLE", "THINKING", "WAITING"},
    "WAITING": {"WAITING", "IDLE", "THINKING"},
}


class ExpressionError(Exception):
    """Closed-set validation failures for the expression domain."""


def validate_state(state: str) -> str:
    if state not in EXPRESSION_STATES:
        raise ExpressionError(
            f"unknown expression state '{state}' "
            f"(allowed: {EXPRESSION_STATES})")
    return state


def transition_allowed(current: str, target: str) -> bool:
    """Self-transitions are allowed (idempotent refresh, §12)."""
    validate_state(current)
    validate_state(target)
    return target in _EXPRESSION_TRANSITIONS[current]


# ---------------------------------------------------------------------------
# ExpressionIntent — "what the body should express now"
# (deliberately NOT an ActionIntent: no capability/action_type/params
#  freedom; just a state + provenance)
# ---------------------------------------------------------------------------
class ExpressionIntent:
    """Immutable intent for the body to express a state."""

    __slots__ = ("state", "reason", "source", "created_at")

    def __init__(self, state: str, reason: str = "",
                 source: str = "system_event", created_at: float = 0.0):
        validate_state(state)
        self.state = state
        self.reason = str(reason or "")[:200]
        self.source = str(source or "system_event")[:60]
        self.created_at = created_at or time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {"state": self.state, "reason": self.reason,
                "source": self.source, "created_at": self.created_at}


# ---------------------------------------------------------------------------
# ExpressionPolicy — closed state -> body-expression mapping
# (the ONLY place states map to hardware operations; LLM never picks
#  operations, GPIO, or raw commands)
# ---------------------------------------------------------------------------
class ExpressionPolicy:
    """Closed mapping: which internal states may drive which device
    expression operation. Default: every state maps to SET_LCD_STATE
    with its own name as the parameter (a 1:1, non-inventive mapping).
    """

    def __init__(self, mapping: Optional[Dict[str, str]] = None):
        base = {s: ("SET_LCD_STATE", s) for s in EXPRESSION_STATES}
        if mapping:
            for state, target in mapping.items():
                validate_state(state)
                if not isinstance(target, tuple) or len(target) != 2:
                    raise ExpressionError("mapping target must be "
                                          "(operation, value)")
                base[state] = target
        self._mapping = base

    def resolve(self, intent: ExpressionIntent):
        """Return (operation, parameters) or raise (closed table)."""
        entry = self._mapping.get(intent.state)
        if entry is None:
            raise ExpressionError(
                f"state '{intent.state}' has no expression mapping "
                f"(closed policy)")
        operation, value = entry
        return operation, {"state": value}
