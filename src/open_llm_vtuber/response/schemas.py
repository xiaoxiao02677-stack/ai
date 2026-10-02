"""P24-A response domain objects: intents, policy, lifecycle."""

import time
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# SpeechIntent — "what to say this round" (intent only: no TTS, no
# audio, no devices)
# ---------------------------------------------------------------------------
class SpeechIntent:
    __slots__ = ("text", "mode", "created_at")

    def __init__(self, text: str, mode: str = "text",
                 created_at: float = 0.0):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("SpeechIntent.text must be a non-empty "
                             "string")
        if len(text) > 2000:
            raise ValueError("SpeechIntent.text too long (max 2000)")
        if mode not in ("text",):   # reserved: future voice modes
            raise ValueError(f"unknown speech mode '{mode}'")
        self.text = text
        self.mode = mode
        self.created_at = created_at or time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "mode": self.mode,
                "created_at": self.created_at}


class MockSpeechAdapter:
    """Deterministic MOCK speech adapter (clearly labeled; never real
    TTS, never touches audio devices). fail_on_text: any substring
    that makes the adapter return FAILED (for tests)."""

    adapter = "mock"

    def __init__(self, fail_on_text: Optional[str] = None):
        self.fail_on_text = fail_on_text
        self.utterances = []

    def speak(self, intent: SpeechIntent) -> Dict[str, Any]:
        failed = (self.fail_on_text is not None
                  and self.fail_on_text in intent.text)
        self.utterances.append(intent.to_dict())
        return {"status": "FAILED" if failed else "SUCCESS",
                "adapter": "mock", "text": intent.text[:200]}


# ---------------------------------------------------------------------------
# CoordinationPolicy — closed set (§11): how the channels combine
# ---------------------------------------------------------------------------
COORDINATION_POLICIES = (
    "NORMAL_RESPONSE",      # speech+expression, no action
    "ACTION_RESPONSE",      # speech+expression+action
    "ERROR_RESPONSE",       # speech+ERROR expression, no action
    "NO_ACTION_RESPONSE",   # speech+WAITING/IDLE expression, no action
)

# channel table per policy (closed; LLM can never invent combinations)
_POLICY_CHANNELS = {
    "NORMAL_RESPONSE":    {"speech": True, "expression": "state",
                           "action": False},
    "ACTION_RESPONSE":    {"speech": True, "expression": "state",
                           "action": True},
    "ERROR_RESPONSE":     {"speech": True, "expression": "ERROR",
                           "action": False},
    "NO_ACTION_RESPONSE": {"speech": True, "expression": "WAITING",
                           "action": False},
}


class CoordinationPolicyError(Exception):
    pass


def validate_policy(name: str) -> str:
    if name not in COORDINATION_POLICIES:
        raise CoordinationPolicyError(
            f"unknown coordination policy '{name}' "
            f"(allowed: {COORDINATION_POLICIES})")
    return name


def policy_channels(name: str) -> Dict[str, Any]:
    """Closed channel table for a policy (a copy; callers cannot
    mutate the policy set)."""
    validate_policy(name)
    return dict(_POLICY_CHANNELS[name])


# ---------------------------------------------------------------------------
# ResponseIntent — the complete response for one round (§6)
# ---------------------------------------------------------------------------
class ResponseIntent:
    """One full response: speech + expression + (optional) action."""

    def __init__(self, speech: SpeechIntent,
                 expression_state: Optional[str] = None,
                 action_request: Optional[Dict[str, Any]] = None,
                 policy: str = "NORMAL_RESPONSE",
                 source_decision_id: str = "",
                 response_id: str = "",
                 created_at: float = 0.0):
        # schema validation (fail closed)
        if not isinstance(speech, SpeechIntent):
            raise ValueError("ResponseIntent.speech must be a "
                             "SpeechIntent")
        validate_policy(policy)
        channels = policy_channels(policy)
        if channels["action"] and not action_request:
            raise ValueError(
                f"policy '{policy}' requires an action_request")
        if not channels["action"] and action_request is not None:
            raise ValueError(
                f"policy '{policy}' must not carry an action_request "
                f"(no-action policies never dispatch actions)")
        if channels["expression"] == "ERROR":
            expression_state = "ERROR"
        elif channels["expression"] == "WAITING":
            expression_state = expression_state or "WAITING"
        if expression_state is not None:
            from open_llm_vtuber.expression.schemas import validate_state
            validate_state(expression_state)
        self.response_id = response_id or ("resp-" + str(int(
            time.time() * 1000))[-10:] + "-" + str(id(self))[-4:])
        self.source_decision_id = str(source_decision_id or "")
        self.speech = speech
        self.expression_state = expression_state
        self.action_request = action_request   # {"operation":..,
        #                                           "parameters":..}
        self.policy = policy
        self.created_at = created_at or time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "response_id": self.response_id,
            "source_decision_id": self.source_decision_id,
            "policy": self.policy,
            "speech": self.speech.to_dict(),
            "expression_state": self.expression_state,
            "action_request": self.action_request,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# ResponseLifecycle — the response-level state machine (§14; distinct
# from the P18 CommandLifecycle which keeps managing device commands)
# ---------------------------------------------------------------------------
RESPONSE_STATES = ("CREATED", "PLANNED", "RUNNING", "COMPLETED",
                   "PARTIAL", "FAILED")

_RESPONSE_TRANSITIONS = {
    "CREATED": {"PLANNED"},
    "PLANNED": {"RUNNING"},
    "RUNNING": {"COMPLETED", "PARTIAL", "FAILED"},
    "COMPLETED": set(),   # terminal
    "PARTIAL": set(),     # terminal
    "FAILED": set(),      # terminal
}


class ResponseLifecycle:
    """Tracks one response's lifecycle; terminal states are final."""

    def __init__(self, response_id: str):
        self.response_id = response_id
        self.state = "CREATED"
        self.history = [("CREATED", time.time())]

    def transition(self, target: str) -> str:
        if target not in RESPONSE_STATES:
            raise ValueError(f"unknown response state '{target}'")
        if target not in _RESPONSE_TRANSITIONS[self.state]:
            raise ValueError(
                f"illegal response transition {self.state} -> {target}")
        self.state = target
        self.history.append((target, time.time()))
        return target
