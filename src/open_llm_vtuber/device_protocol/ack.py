"""DeviceAck: the typed device->server acknowledgement (Phase 16).

The FORMAL response side of the device protocol. An ACK is what a
real device (ESP32 firmware) returns after validating and (for
ACK status) executing a DeviceCommand. It carries the machine-
interpretable outcome plus an ENUM error code — never free-form
exception strings as protocol.

Closed schema by construction; from_dict rejects unknown keys.
Status is a controlled enum (ACK / NACK); error_code is a controlled
enum aligned with the firmware's rejection reasons.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

PROTOCOL_VERSION = 1

# controlled status enum
ACK_STATUS = ("ACK", "NACK")

# controlled error-code enum (server + firmware share this table;
# None means success)
ACK_ERROR_CODES = (
    "UNKNOWN_COMMAND",
    "DUPLICATE_COMMAND",
    "UNKNOWN_DEVICE",
    "UNKNOWN_OPERATION",
    "INVALID_PARAMETERS",
    "INVALID_SCHEMA",
    "UNSUPPORTED_VERSION",
    "MALFORMED_MESSAGE",
    "TIMEOUT",
)

_MAX_ECHO_PARAMS = 5
_MAX_ECHO_LEN = 200


class DeviceAckError(ValueError):
    """Raised on any ACK schema violation (fail closed)."""


@dataclass
class DeviceAck:
    command_id: str
    device_id: str
    status: str                        # ACK_STATUS enum
    error_code: Optional[str] = None   # None on success
    protocol_version: int = PROTOCOL_VERSION
    message_type: str = "ack"
    echo: Dict[str, str] = field(default_factory=dict)  # optional op echo
    created_at: float = 0.0

    # envelope-level errors: rejected before the command could be parsed,
    # so the device legitimately does not know the command_id yet
    _ENVELOPE_LEVEL_ERRORS = ("UNSUPPORTED_VERSION", "MALFORMED_MESSAGE",
                              "INVALID_SCHEMA")

    # -- validation -----------------------------------------------------------

    def validate(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise DeviceAckError(
                f"unsupported ack protocol_version {self.protocol_version} "
                f"(only {PROTOCOL_VERSION})")
        if self.message_type != "ack":
            raise DeviceAckError(
                f"ack message_type must be 'ack' (got '{self.message_type}')")
        if not self.device_id or not isinstance(self.device_id, str):
            raise DeviceAckError("device_id must be a non-empty string")
        if self.status not in ACK_STATUS:
            raise DeviceAckError(
                f"unknown ack status '{self.status}' (allowed: {ACK_STATUS})")
        if self.status == "ACK":
            if self.error_code is not None:
                raise DeviceAckError(
                    "ACK status cannot carry an error_code")
            if not self.command_id or not isinstance(self.command_id, str):
                raise DeviceAckError(
                    "ACK requires a non-empty command_id (device executed "
                    "it, so it knows the id)")
        if self.status == "NACK":
            if self.error_code not in ACK_ERROR_CODES:
                raise DeviceAckError(
                    f"NACK requires a valid error_code from {ACK_ERROR_CODES} "
                    f"(got {self.error_code!r})")
            empty_ok = self.error_code in self._ENVELOPE_LEVEL_ERRORS
            if not empty_ok and not self.command_id:
                raise DeviceAckError(
                    f"NACK {self.error_code} requires the command_id "
                    f"(it was parsed); only {self._ENVELOPE_LEVEL_ERRORS} "
                    f"may carry an empty command_id")
        if not isinstance(self.echo, dict) or len(self.echo) > _MAX_ECHO_PARAMS:
            raise DeviceAckError(
                f"echo must be a dict of at most {_MAX_ECHO_PARAMS} params")
        for key, value in self.echo.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise DeviceAckError("echo entries must be string->string")
            if len(value) > _MAX_ECHO_LEN:
                raise DeviceAckError(
                    f"echo value for '{key}' exceeds {_MAX_ECHO_LEN} chars")

    @property
    def is_success(self) -> bool:
        return self.status == "ACK" and self.error_code is None

    # -- serialization -----------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "device_id": self.device_id,
            "status": self.status,
            "error_code": self.error_code,
            "protocol_version": self.protocol_version,
            "message_type": self.message_type,
            "echo": dict(self.echo),
            "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "DeviceAck":
        allowed = {"command_id", "device_id", "status", "error_code",
                   "protocol_version", "message_type", "echo", "created_at"}
        unknown = set(d.keys()) - allowed
        if unknown:
            raise DeviceAckError(
                f"unknown DeviceAck fields {sorted(unknown)} "
                f"(closed schema: {sorted(allowed)})")
        ack = DeviceAck(
            command_id=str(d.get("command_id", "")),
            device_id=str(d.get("device_id", "")),
            status=str(d.get("status", "")),
            error_code=d.get("error_code"),
            protocol_version=int(d.get("protocol_version", 0)),
            message_type=str(d.get("message_type", "")),
            echo=dict(d.get("echo") or {}),
            created_at=float(d.get("created_at", 0.0)),
        )
        ack.validate()
        return ack
