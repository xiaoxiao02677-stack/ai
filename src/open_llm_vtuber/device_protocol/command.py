"""DeviceCommand: the typed, closed-schema server-to-device command.

Phase 15 domain — the Device Protocol LAYER object, strictly separate
from ActionIntent (AI/decision layer). A DeviceCommand is what an
ESP32Adapter produces from an already-authorized ExecutionRequest:
minimal, type-safe, bounded and fully validatable. It never carries
natural language, executables, URLs or free-form JSON.

Closed schema by construction: the dataclass fields ARE the schema;
from_dict rejects unknown keys (additionalProperties=false) and every
field is typed and length-bounded. Operation is a CONTROLLED ENUM —
Phase 15 ships exactly one protocol-level test operation (TEST_ECHO),
explicitly marked TEST/MOCK ONLY; business operations belong to
future phases with contract review.

command_id is a DETERMINISTIC derivation of the execution context
(action/decision/evaluation/strategy ids + operation + device) —
same context, same id (idempotency for the timeout-retry race);
different context, different id. Not a random UUID.
"""

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# protocol version (unknown versions are REJECTED, no auto-upgrade)
PROTOCOL_VERSION = 1

# controlled operation enum — minimal Phase-15 set
DEVICE_OPERATIONS = ("TEST_ECHO",)   # TEST / MOCK ONLY (no business ops yet)

_MAX_PARAM_VALUE_LEN = 200
_MAX_PARAMS = 5


class DeviceCommandError(ValueError):
    """Raised on any schema/protocol violation (fail closed)."""


def derive_command_id(action_id: str, decision_id: str,
                      evaluation_id: str, strategy_id: str,
                      operation: str, device_id: str) -> str:
    """Deterministic idempotency key from the execution context.

    Same context -> same id; any differing component -> different id.
    SHA-1 truncated to 16 hex chars: collision-resistant within the
    defined scope (per-context uniqueness), not claimed absolutely
    collision-free.
    """
    material = "|".join((action_id, decision_id, evaluation_id,
                         strategy_id, operation, device_id))
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]


def _validate_parameters(parameters: Dict[str, Any]) -> None:
    if not isinstance(parameters, dict):
        raise DeviceCommandError("parameters must be a dict")
    if len(parameters) > _MAX_PARAMS:
        raise DeviceCommandError(
            f"too many parameters ({len(parameters)} > {_MAX_PARAMS})")
    for key, value in parameters.items():
        if not isinstance(key, str) or not key:
            raise DeviceCommandError("parameter keys must be non-empty strings")
        if not isinstance(value, str):
            raise DeviceCommandError(
                f"parameter '{key}' must be a string "
                f"(got {type(value).__name__})")
        if len(value) > _MAX_PARAM_VALUE_LEN:
            raise DeviceCommandError(
                f"parameter '{key}' exceeds {_MAX_PARAM_VALUE_LEN} chars")


@dataclass
class DeviceCommand:
    command_id: str
    device_id: str
    capability: str                       # capability_id (vendor-free)
    operation: str                        # DEVICE_OPERATIONS enum
    parameters: Dict[str, str] = field(default_factory=dict)
    protocol_version: int = PROTOCOL_VERSION
    provenance: Dict[str, str] = field(default_factory=dict)  # 4 ids
    created_at: float = 0.0

    # -- validation -----------------------------------------------------------

    def validate(self) -> None:
        """Raise DeviceCommandError on any violation (fail closed)."""
        if not self.command_id or not isinstance(self.command_id, str):
            raise DeviceCommandError("command_id must be a non-empty string")
        if not self.device_id or not isinstance(self.device_id, str):
            raise DeviceCommandError("device_id must be a non-empty string")
        # device_id is an AI-layer identity: no addresses as business ids
        low = self.device_id.lower()
        for marker in ("/", ":", "@", "http", "tcp", "udp", ".local",
                       "192.168.", "10.0.", "127.0"):
            if marker in low:
                raise DeviceCommandError(
                    f"device_id '{self.device_id}' looks like a transport "
                    f"address — device identity is business-scoped, not "
                    f"an address")
        if not self.capability or not isinstance(self.capability, str):
            raise DeviceCommandError("capability must be a non-empty string")
        if self.operation not in DEVICE_OPERATIONS:
            raise DeviceCommandError(
                f"unknown operation '{self.operation}' "
                f"(allowed: {DEVICE_OPERATIONS})")
        if self.protocol_version != PROTOCOL_VERSION:
            raise DeviceCommandError(
                f"unsupported protocol_version {self.protocol_version} "
                f"(only {PROTOCOL_VERSION}; no auto-upgrade/downgrade)")
        _validate_parameters(self.parameters)
        required_prov = ("action_id", "decision_id", "evaluation_id",
                         "strategy_id")
        for key in required_prov:
            val = self.provenance.get(key)
            if not val or not isinstance(val, str):
                raise DeviceCommandError(
                    f"provenance.{key} is required (full traceability)")
        for key in self.provenance:
            if key not in required_prov:
                raise DeviceCommandError(
                    f"unknown provenance key '{key}' "
                    f"(closed schema: {required_prov})")

    # -- serialization -----------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "device_id": self.device_id,
            "capability": self.capability,
            "operation": self.operation,
            "parameters": dict(self.parameters),
            "protocol_version": self.protocol_version,
            "provenance": dict(self.provenance),
            "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "DeviceCommand":
        """Closed-schema reconstruction: unknown top-level keys rejected."""
        allowed = {"command_id", "device_id", "capability", "operation",
                   "parameters", "protocol_version", "provenance",
                   "created_at"}
        unknown = set(d.keys()) - allowed
        if unknown:
            raise DeviceCommandError(
                f"unknown DeviceCommand fields {sorted(unknown)} "
                f"(closed schema: {sorted(allowed)})")
        cmd = DeviceCommand(
            command_id=str(d.get("command_id", "")),
            device_id=str(d.get("device_id", "")),
            capability=str(d.get("capability", "")),
            operation=str(d.get("operation", "")),
            parameters=dict(d.get("parameters") or {}),
            protocol_version=int(d.get("protocol_version", 0)),
            provenance=dict(d.get("provenance") or {}),
            created_at=float(d.get("created_at", 0.0)),
        )
        cmd.validate()
        return cmd

    @staticmethod
    def from_request(request, device_id: str,
                     operation: str = "TEST_ECHO") -> "DeviceCommand":
        """Build from an ExecutionRequest (ESP32Adapter path).

        command_id derives deterministically from the provenance +
        operation + device — idempotency by construction.
        """
        prov = {
            "action_id": request.action_id,
            "decision_id": request.decision_id,
            "evaluation_id": request.evaluation_id,
            "strategy_id": request.strategy_id,
        }
        cmd = DeviceCommand(
            command_id=derive_command_id(
                request.action_id, request.decision_id,
                request.evaluation_id, request.strategy_id,
                operation, device_id),
            device_id=device_id,
            capability=request.capability_id,
            operation=operation,
            parameters=dict(request.parameters),
            protocol_version=PROTOCOL_VERSION,
            provenance=prov,
            created_at=time.time(),
        )
        cmd.validate()
        return cmd
