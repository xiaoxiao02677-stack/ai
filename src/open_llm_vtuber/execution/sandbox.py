"""SandboxExecutor: strictly-controlled deterministic simulation.

THE security boundary of Phase 11. The executor NEVER trusts the
ActionIntent — it re-validates everything (status / action_type /
parameters / provenance / conf_uid) and refuses anything that does
not match BOTH the Phase-10 action vocabulary AND an independent
Phase-11 sandbox whitelist.

Parameter validation is a STRICT SCHEMA per action type (upgrading
Phase 10's LOW finding): every key must be whitelisted, typed and
length-bounded; unknown keys are rejected. There is no blacklist
parsing, no string heuristics as the only guard, and absolutely no
dynamic execution — the simulation is a pure function of the intent
(no eval/exec/subprocess/network/filesystem/random; timestamps only
label records, never change outcomes).
"""

from typing import Any, Dict, Optional, Tuple

from loguru import logger

from ..action.schemas import (ActionIntentRecord, ACTION_TYPES as
                              P10_ACTION_TYPES, STATUS_PLANNED as
                              INTENT_PLANNED)
from .schemas import (ExecutionResult, STATUS_SIMULATED, STATUS_REJECTED)

# independent Phase-11 sandbox whitelist (must ALSO be a P10 type)
SANDBOX_ACTION_TYPES = ("RESPOND", "REMIND", "ACKNOWLEDGE")

# strict per-type parameter schema: key -> (python type, max length)
# unknown keys are REJECTED — parameters are closed structs, not open dicts
_PARAM_SCHEMAS: Dict[str, Dict[str, Tuple[type, int]]] = {
    "RESPOND": {
        "style_hint": (str, 200),
        "condition_hint": (str, 120),
    },
    "REMIND": {
        "topic_hint": (str, 120),
        "condition_hint": (str, 120),
    },
    "ACKNOWLEDGE": {
        "condition_hint": (str, 120),
    },
}

_MAX_PARAMS = 5          # hard cap on parameter count
_MAX_VALUE_LEN = 200     # hard cap on any string value


def _validate_parameters(action_type: str,
                         parameters: Dict[str, Any]) -> Optional[str]:
    """Return an error string, or None when the parameters conform."""
    if not isinstance(parameters, dict):
        return "parameters must be a structured dict"
    if len(parameters) > _MAX_PARAMS:
        return f"too many parameters ({len(parameters)} > {_MAX_PARAMS})"
    schema = _PARAM_SCHEMAS.get(action_type)
    if schema is None:
        return f"no sandbox parameter schema for action_type '{action_type}'"
    for key, value in parameters.items():
        if key not in schema:
            return (f"parameter key '{key}' is not in the sandbox schema "
                    f"for {action_type} (allowed: {sorted(schema)})")
        expected_type, max_len = schema[key]
        if not isinstance(value, expected_type):
            return (f"parameter '{key}' must be {expected_type.__name__}, "
                    f"got {type(value).__name__}")
        if isinstance(value, str) and len(value) > max_len:
            return (f"parameter '{key}' exceeds {max_len} chars "
                    f"(got {len(value)})")
        if isinstance(value, str) and len(value) > _MAX_VALUE_LEN:
            return f"parameter value exceeds {_MAX_VALUE_LEN} chars"
    return None


class SandboxExecutor:
    """Deterministic, side-effect-free simulation of an action intent."""

    def execute(self, intent: ActionIntentRecord,
                conf_uid: str) -> ExecutionResult:
        """Simulate one intent. The ONLY entry; never performs anything."""
        rec = ExecutionResult.new(conf_uid, intent.action_id, STATUS_REJECTED)
        # carry the full provenance chain from the intent (verified by the
        # engine before this point; recorded here for traceability)
        rec.decision_id = intent.decision_id
        rec.evaluation_id = intent.evaluation_id
        rec.strategy_id = intent.strategy_id

        # gate 1: status — only planned intents enter the sandbox
        if intent.status != INTENT_PLANNED:
            rec.reason = (f"intent status is '{intent.status}' — only "
                          f"'{INTENT_PLANNED}' intents may enter the sandbox")
            return rec

        # gate 2: conf scope
        if intent.conf_uid != conf_uid:
            rec.reason = "intent conf_uid does not match the executing conf"
            return rec

        # gate 3: action_type must satisfy BOTH whitelists
        if intent.action_type not in SANDBOX_ACTION_TYPES:
            rec.reason = (f"action_type '{intent.action_type}' is not in the "
                          f"sandbox whitelist {SANDBOX_ACTION_TYPES}")
            return rec
        if intent.action_type not in P10_ACTION_TYPES:  # defense in depth
            rec.reason = (f"action_type '{intent.action_type}' is not a "
                          f"Phase-10 action type")
            return rec

        # gate 4: provenance presence (equality with the chain is verified
        # by the engine against the stored records; presence here is the
        # executor's own re-check)
        if not (intent.decision_id and intent.evaluation_id
                and intent.strategy_id):
            rec.reason = "intent provenance incomplete (decision/evaluation/" \
                         "strategy ids required)"
            return rec

        # gate 5: STRICT parameter schema (closed struct per type)
        param_error = _validate_parameters(intent.action_type,
                                            intent.parameters)
        if param_error:
            rec.reason = f"parameter validation failed: {param_error}"
            return rec

        # deterministic simulation — pure function of the intent
        try:
            rec.status = STATUS_SIMULATED
            rec.result = {
                "simulated": True,
                "action_type": intent.action_type,
                "parameters": dict(intent.parameters),
            }
            rec.reason = (
                f"sandbox 模拟完成：{intent.action_type} 意图通过全部校验，"
                f"未产生任何真实副作用。")
            rec.evidence = [intent.action_id]
            rec.metadata = {"executor": "sandbox"}
        except Exception as e:  # noqa: BLE001
            # internal failure NEVER masquerades as SIMULATED
            rec.status = "FAILED"
            rec.result = {}
            rec.reason = f"sandbox internal error: {e}"
            logger.error(f"[SBX] sandbox failure for {intent.action_id}: {e}")
        return rec
