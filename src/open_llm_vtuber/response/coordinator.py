"""P24-A ResponseCoordinator + ResponseObservation + memory wiring."""

import time
from typing import Any, Callable, Dict, Optional

try:
    from src.open_llm_vtuber.expression.schemas import ExpressionIntent
    from src.open_llm_vtuber.expression.gateway import (
        ExpressionGateway, ExpressionResult)
except ImportError:
    from open_llm_vtuber.expression.schemas import ExpressionIntent  # type: ignore
    from open_llm_vtuber.expression.gateway import (  # type: ignore
        ExpressionGateway, ExpressionResult)

from .schemas import (SpeechIntent, MockSpeechAdapter, ResponseIntent,
                      ResponseLifecycle, policy_channels)


class ResponseObservation(dict):
    """The observation of one complete response (channel results kept
    SEPARATE, correlated by id — never re-storing full results)."""


class ResponseCoordinator:
    """Coordinates the three channels for one ResponseIntent.

    Channel independence (§13): each channel result is kept as-is;
    the lifecycle aggregates WITHOUT merging.

    Boundaries: speech -> MockSpeechAdapter; expression -> the P23
    ExpressionGateway; action -> the provided action runner (the
    P21/P20 path — the coordinator never builds device commands or
    touches transports).
    """

    def __init__(self,
                 speech_adapter: Optional[MockSpeechAdapter] = None,
                 expression_gateway: Optional[ExpressionGateway] = None,
                 action_runner: Optional[Callable[..., Dict]] = None):
        self.speech_adapter = speech_adapter or MockSpeechAdapter()
        self.expression_gateway = expression_gateway
        # action_runner(user_message, conf_uid, **kwargs) -> P21-style
        # run dict (execution_status/device_ack/command_id/...). When
        # None, action policies cannot dispatch (fail closed).
        self.action_runner = action_runner
        # response history (bounded, real objects only)
        self.responses = []

    def coordinate(self, intent: ResponseIntent,
                   user_message: str = "",
                   conf_uid: str = "p24a",
                   **action_kwargs) -> Dict[str, Any]:
        channels = policy_channels(intent.policy)
        lifecycle = ResponseLifecycle(intent.response_id)
        lifecycle.transition("PLANNED")

        results: Dict[str, Any] = {"speech": None, "expression": None,
                                   "action": None}

        # ---- speech channel (mock) --------------------------------
        if channels["speech"]:
            results["speech"] = self.speech_adapter.speak(intent.speech)

        # ---- expression channel (P23 gateway) ---------------------
        if channels["expression"] and self.expression_gateway is not None:
            expr_state = intent.expression_state or "IDLE"
            results["expression"] = self.expression_gateway.express(
                ExpressionIntent(state=expr_state,
                                 reason="response coordination",
                                 source="response"))

        lifecycle.transition("RUNNING")

        # ---- action channel (P13/P21 path) --------------------------
        if channels["action"]:
            if self.action_runner is None:
                results["action"] = {"execution_status": "NOT_DISPATCHED",
                                     "reason": "no action runner wired"}
            else:
                results["action"] = self.action_runner(
                    user_message or intent.speech.text,
                    conf_uid=conf_uid, **action_kwargs)
            # expression follow-through (§17): ACKED -> SUCCESS,
            # otherwise ERROR — via the SAME P23 gateway
            if self.expression_gateway is not None:
                run = results["action"] or {}
                ack = run.get("device_ack")
                follow = ("SUCCESS"
                          if isinstance(ack, dict)
                          and ack.get("status") == "ACK" else "ERROR")
                results["expression_follow"] = \
                    self.expression_gateway.express(
                        ExpressionIntent(state=follow,
                                         reason="action outcome",
                                         source="response"))

        # ---- aggregate (never merge; compute the overall state) ----
        channel_status = []
        for key in ("speech", "expression", "action"):
            r = results.get(key)
            if r is None:
                continue   # channel not part of this policy
            status = (r.get("status") or r.get("execution_status")
                      or "UNKNOWN")
            channel_status.append((key, status))
        necessary = [s for _, s in channel_status] or ["NONE"]
        succeeded = [s for s in necessary
                     if s in ("SUCCESS", "ACKED", "EXECUTED",
                              "SIMULATED", "COMPLETED")]
        failed = [s for s in necessary
                  if s not in ("SUCCESS", "ACKED", "EXECUTED",
                               "SIMULATED", "COMPLETED")]
        if not succeeded and failed:
            overall = "FAILED"
        elif failed:
            overall = "PARTIAL"
        else:
            overall = "COMPLETED"
        lifecycle.transition(overall)

        observation = ResponseObservation({
            "response_id": intent.response_id,
            "decision_id": intent.source_decision_id,
            "policy": intent.policy,
            "speech_result": results.get("speech"),
            "expression_result": results.get("expression"),
            "expression_follow": results.get("expression_follow"),
            "action_result": (None if results.get("action") is None else {
                "execution_status":
                    results["action"].get("execution_status"),
                "command_id": results["action"].get("command_id"),
                "action_intent_id":
                    results["action"].get("action_intent_id"),
                "device_ack": results["action"].get("device_ack"),
            }),
            "response_status": overall,
            "timestamp": time.time(),
        })
        self.responses.append({
            "response_id": intent.response_id,
            "status": overall,
            "speech_status": (results.get("speech") or {}).get("status"),
            "expression_state": (results.get("expression") or {})
            .get("state"),
            "action_status": (results.get("action") or {})
            .get("execution_status"),
            "created_at": intent.created_at,
            "updated_at": time.time(),
            "lifecycle": [s for s, _ in lifecycle.history],
        })
        if len(self.responses) > 50:
            del self.responses[:25]
        return {"lifecycle": lifecycle,
                "results": results,
                "observation": observation,
                "overall": overall}


# ---------------------------------------------------------------------------
# ResponseObservation -> Memory (reuse the P22 experience channel)
# ---------------------------------------------------------------------------
def observe_response(conf_uid: str, coordination: Dict[str, Any],
                     user_message: str = "") -> ResponseObservation:
    """Persist the ResponseObservation through the EXISTING P22 memory
    channel (an experience row carrying metadata.p24_response_obs)."""
    obs = coordination.get("observation")
    if obs is None:
        return ResponseObservation({})
    try:
        from src.open_llm_vtuber.experience.engine import \
            ExperienceEngine
        from src.open_llm_vtuber.experience.repository import \
            ExperienceRepository
        from src.open_llm_vtuber.long_term_memory.store import MemoryStore
    except ImportError:
        from open_llm_vtuber.experience.engine import \
            ExperienceEngine  # type: ignore
        from open_llm_vtuber.experience.repository import (  # type: ignore
            ExperienceRepository)
        from open_llm_vtuber.long_term_memory.store import \
            MemoryStore  # type: ignore
    engine = ExperienceEngine.start(conf_uid, interaction_type="proactive")
    engine.record_user_input("")
    engine.record_ai_response("")
    speech_status = (obs.get('speech_result') or {}).get('status')
    action_status = (obs.get('action_result') or {}).get(
        'execution_status')
    engine.record_outcome(
        "[P24 Response] %s (speech=%s, action=%s)" % (
            obs.get('response_status'), speech_status, action_status))
    exp = engine.finalize()
    meta = dict(exp.metadata or {})
    meta["p24_response_obs"] = dict(obs)
    exp.metadata = meta
    ExperienceRepository(MemoryStore(conf_uid).provider).add(exp)
    return obs


def recall_recent_responses(conf_uid: str,
                            limit: int = 10):
    """Retrieve recent ResponseObservations through the REAL memory
    query (P22 pattern; no globals)."""
    try:
        from src.open_llm_vtuber.experience.repository import \
            ExperienceRepository
        from src.open_llm_vtuber.long_term_memory.store import MemoryStore
    except ImportError:
        from open_llm_vtuber.experience.repository import (  # type: ignore
            ExperienceRepository)
        from open_llm_vtuber.long_term_memory.store import \
            MemoryStore  # type: ignore
    repo = ExperienceRepository(MemoryStore(conf_uid).provider)
    out = []
    for exp in repo.list_recent(limit=200):
        obs = (exp.metadata or {}).get("p24_response_obs")
        if isinstance(obs, dict):
            out.append(obs)
        if len(out) >= limit:
            break
    return out
