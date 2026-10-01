"""SandboxExecutor: strictly-controlled deterministic simulation.

THE security boundary of Phase 11/12. The executor NEVER trusts the
ActionIntent — it re-validates everything and refuses anything that
does not pass ALL gates.

Phase 12 adds the capability layer to the gate chain: the executor
resolves the intent's action_type through the (deterministic)
CapabilityResolver, then validates the intent's parameters against the
resolved CapabilityContract's CLOSED input schema — replacing the
Phase-11 local parameter table with the contract as the single
parameter authority. The legacy per-type table is retained ONLY as a
secondary defense-in-depth check (both must pass; identical contents).

Gates (any failure -> REJECTED):
  1. intent status == planned
  2. conf scope match
  3. action_type in the Phase-11 sandbox whitelist AND Phase-10 vocabulary
  4. provenance presence (engine verifies equality against stored records)
  5. capability RESOLVED (UNSUPPORTED/DISABLED/INVALID -> REJECTED)
  6. action_type within contract.allowed_action_types (mismatch -> REJECTED)
  7. parameters match the CONTRACT input schema (closed struct)
  8. parameters ALSO match the local strict table (defense in depth)
  9. deterministic simulation (pure function; internal error -> FAILED)
"""

from typing import Any, Dict, Optional, Tuple

from loguru import logger

from ..action.schemas import (ActionIntentRecord, ACTION_TYPES as
                              P10_ACTION_TYPES, STATUS_PLANNED as
                              INTENT_PLANNED)
from ..capability.resolver import CapabilityResolver
from ..capability import get_resolver
from ..capability.schemas import RESOLVED as CAP_RESOLVED
from .schemas import (ExecutionResult, STATUS_SIMULATED, STATUS_REJECTED)

# independent Phase-11 sandbox whitelist (must ALSO be a P10 type)
# Phase 19 adds SET_LED (body-intent; 'on' is a bool — the schema below
# enforces the closed structure; the sandbox only SIMULATES it)
SANDBOX_ACTION_TYPES = ("RESPOND", "REMIND", "ACKNOWLEDGE", "SET_LED")

# legacy strict per-type parameter schema — kept as defense-in-depth;
# the Phase-12 CONTRACT input schema is the primary authority now
# NOTE: SET_LED's 'on' is bool — max_len is unused for bools
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
    "SET_LED": {
        "on": (bool, 0),
    },
}

_MAX_PARAMS = 5          # hard cap on parameter count
_MAX_VALUE_LEN = 200     # hard cap on any string value


def _validate_parameters_legacy(action_type: str,
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
    return None


class SandboxExecutor:
    """Deterministic, side-effect-free simulation of an action intent."""

    def __init__(self, resolver: Optional[CapabilityResolver] = None):
        self.resolver = resolver if resolver is not None else get_resolver()

    def execute(self, intent: ActionIntentRecord,
                conf_uid: str) -> ExecutionResult:
        """Simulate one intent. The ONLY entry; never performs anything."""
        rec = ExecutionResult.new(conf_uid, intent.action_id, STATUS_REJECTED)
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

        # gate 4: provenance presence (equality verified by the engine)
        if not (intent.decision_id and intent.evaluation_id
                and intent.strategy_id):
            rec.reason = "intent provenance incomplete (decision/evaluation/" \
                         "strategy ids required)"
            return rec

        # gate 5 (Phase 12): capability resolution — deterministic, no fallback
        resolution = self.resolver.resolve(intent.action_type)
        if resolution.status != CAP_RESOLVED:
            rec.reason = (f"capability resolution failed "
                          f"({resolution.status}): {resolution.reason}")
            return rec
        contract = resolution.capability

        # gate 6 (Phase 12): action_type must be allowed by the CONTRACT
        if intent.action_type not in contract.allowed_action_types:
            rec.reason = (f"action_type '{intent.action_type}' is not allowed "
                          f"by capability '{contract.capability_id}' "
                          f"(allowed: {contract.allowed_action_types}) — "
                          f"no auto-correction, no fallback")
            return rec

        # gate 7 (Phase 12): CONTRACT closed input schema (primary authority)
        schema_error = contract.validate_input(intent.parameters)
        if schema_error:
            rec.reason = f"contract input validation failed: {schema_error}"
            return rec

        # gate 8: legacy strict table — defense in depth (must also pass)
        legacy_error = _validate_parameters_legacy(intent.action_type,
                                                   intent.parameters)
        if legacy_error:
            rec.reason = f"parameter validation failed: {legacy_error}"
            return rec

        # deterministic simulation — pure function of the intent + contract
        try:
            rec.status = STATUS_SIMULATED
            rec.result = {
                "simulated": True,
                "action_type": intent.action_type,
                "capability_id": contract.capability_id,
                "capability_version": contract.version,
                "parameters": dict(intent.parameters),
            }
            rec.reason = (
                f"sandbox 模拟完成：{intent.action_type} 意图通过全部校验"
                f"（capability {contract.capability_id} v{contract.version}），"
                f"未产生任何真实副作用。")
            rec.evidence = [intent.action_id]
            rec.metadata = {"executor": "sandbox",
                            "capability_id": contract.capability_id}
        except Exception as e:  # noqa: BLE001
            rec.status = "FAILED"
            rec.result = {}
            rec.reason = f"sandbox internal error: {e}"
            logger.error(f"[SBX] sandbox failure for {intent.action_id}: {e}")
        return rec
