"""CapabilityContract: what a capability IS and what I/O shape it allows.

Phase 12 domain schema — a CONTRACT, not an executable. A contract
describes one capability semantically (respond / remind / acknowledge),
the closed input shape an intent's parameters must conform to, and the
output shape a future executor WOULD produce. Contracts carry no
handler/function/callback/URL — they are pure data descriptions; the
sandbox executor validates against them and simulates.

Schemas are closed structs (additionalProperties=false by construction):
only whitelisted keys with explicit types and length bounds pass.
There is deliberately no blacklist here — the closed structure IS the
boundary (per Phase 11's §-upgraded lesson).
"""

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# resolution outcomes (kept minimal per spec)
RESOLVED = "RESOLVED"
UNSUPPORTED = "UNSUPPORTED"
DISABLED = "DISABLED"
INVALID = "INVALID"
RESOLUTION_STATUSES = (RESOLVED, UNSUPPORTED, DISABLED, INVALID)

_CAP_ID_RX = re.compile(r"^capability\.[a-z][a-z0-9_]*$")


def _validate_field_schema(name: str, fs: Dict[str, Any]) -> None:
    """Validate one field-schema entry: {type, max_length, required}."""
    if not isinstance(fs, dict):
        raise ValueError(f"field schema '{name}' must be a dict")
    ftype = fs.get("type")
    if ftype not in ("string",):
        raise ValueError(
            f"field '{name}': only 'string' fields are supported in "
            f"Phase 12 contracts (got '{ftype}')")
    if not isinstance(fs.get("max_length"), int) or fs["max_length"] <= 0:
        raise ValueError(f"field '{name}' needs a positive max_length")
    if not isinstance(fs.get("required"), bool):
        raise ValueError(f"field '{name}' needs a boolean required flag")
    desc = fs.get("description", "")
    if not isinstance(desc, str):
        raise ValueError(f"field '{name}' description must be a string")


@dataclass
class CapabilityContract:
    capability_id: str                       # e.g. "capability.respond"
    capability_type: str                     # semantic: RESPOND/REMIND/...
    version: str = "1"
    description: str = ""
    input_schema: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    output_schema: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    allowed_action_types: List[str] = field(default_factory=list)
    execution_mode: str = "SANDBOX"          # only mode that exists
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    # -- validation ------------------------------------------------------------

    def validate(self) -> None:
        """Raise ValueError when the contract violates Phase-12 boundaries."""
        if not _CAP_ID_RX.match(self.capability_id):
            raise ValueError(
                f"capability_id '{self.capability_id}' must match "
                f"'capability.<name>' (lowercase, vendor-free)")
        if not self.capability_type:
            raise ValueError("capability_type is required")
        if not re.match(r"^\d+(\.\d+)?$", self.version):
            raise ValueError(f"version '{self.version}' must be numeric")
        if self.execution_mode != "SANDBOX":
            raise ValueError(
                f"execution_mode must be SANDBOX (got '{self.execution_mode}') "
                f"— no other mode exists")
        if not self.allowed_action_types:
            raise ValueError("allowed_action_types must be non-empty")
        # closed input schema: every field fully described
        for name, fs in self.input_schema.items():
            _validate_field_schema(name, fs)
        for name, fs in self.output_schema.items():
            _validate_field_schema(name, fs)
        # contracts are data: forbid executable-looking keys in the schema
        for section in (self.input_schema, self.output_schema):
            for key in section:
                if key.lower() in ("handler", "function", "executor",
                                   "callback", "command", "shell", "url",
                                   "module", "import", "code", "script"):
                    raise ValueError(
                        f"schema field '{key}' has execution semantics — "
                        f"contracts describe data shapes, never execution")

    # -- input validation (the closed-structure boundary) -----------------------

    def validate_input(self, parameters: Dict[str, Any]) -> Optional[str]:
        """Return an error string, or None when parameters conform.

        Closed structure: unknown keys rejected (additionalProperties=
        false), known keys must be typed strings within max_length,
        required keys must be present. This is THE parameter boundary —
        no blacklist heuristics.
        """
        if not isinstance(parameters, dict):
            return "parameters must be a structured dict"
        for key in parameters:
            if key not in self.input_schema:
                return (f"parameter key '{key}' is not in the "
                        f"{self.capability_id} input schema "
                        f"(allowed: {sorted(self.input_schema)})")
        for name, fs in self.input_schema.items():
            if fs["required"] and name not in parameters:
                return (f"required parameter '{name}' is missing for "
                        f"{self.capability_id}")
        for name, value in parameters.items():
            fs = self.input_schema[name]
            if not isinstance(value, str):
                return (f"parameter '{name}' must be string "
                        f"(got {type(value).__name__})")
            if len(value) > fs["max_length"]:
                return (f"parameter '{name}' exceeds max_length "
                        f"{fs['max_length']} (got {len(value)})")
        return None

    # -- serialization ----------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "capability_type": self.capability_type,
            "version": self.version,
            "description": self.description,
            "input_schema": {k: dict(v) for k, v in self.input_schema.items()},
            "output_schema": {k: dict(v) for k, v in self.output_schema.items()},
            "allowed_action_types": list(self.allowed_action_types),
            "execution_mode": self.execution_mode,
            "enabled": self.enabled,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class ResolutionResult:
    """Minimal resolver outcome (spec §13/§14)."""
    status: str                    # RESOLUTION_STATUSES
    capability: Optional[CapabilityContract] = None
    capability_id: str = ""
    reason: str = ""

    def validate(self) -> None:
        if self.status not in RESOLUTION_STATUSES:
            raise ValueError(f"invalid resolution status '{self.status}'")
        if self.status == RESOLVED and self.capability is None:
            raise ValueError("RESOLVED requires a contract")
