"""DeviceProtocol: deterministic command <-> message codec + validator.

Pure data layer — network-free, device-free. encode() turns a
validated DeviceCommand into a JSON message (string); decode() parses
a message back into a DeviceCommand, validating EVERYTHING (schema,
version, operation, parameters, provenance, ids). Malformed or
unknown data raises DeviceCommandError — no auto-repair, no guessing,
no fallback.
"""

import json
from typing import Any, Dict

from .command import DeviceCommand, DeviceCommandError, PROTOCOL_VERSION

# message envelope: {"v": <protocol_version>, "cmd": <DeviceCommand dict>}
_ENVELOPE_KEYS = {"v", "cmd"}


class DeviceProtocol:
    """Deterministic DeviceCommand <-> message codec."""

    def encode(self, command: DeviceCommand) -> str:
        """Validated command -> envelope message string."""
        command.validate()  # never encode an invalid command
        envelope = {"v": command.protocol_version, "cmd": command.to_dict()}
        return json.dumps(envelope, ensure_ascii=False, sort_keys=True)

    def decode(self, message: str) -> DeviceCommand:
        """Message string -> validated DeviceCommand (raises on ANY flaw)."""
        try:
            data = json.loads(message)
        except (json.JSONDecodeError, TypeError) as e:
            raise DeviceCommandError(f"malformed message: {e}") from e
        if not isinstance(data, dict):
            raise DeviceCommandError("message must be a JSON object")
        unknown = set(data.keys()) - _ENVELOPE_KEYS
        if unknown:
            raise DeviceCommandError(
                f"unknown envelope fields {sorted(unknown)}")
        if "v" not in data or "cmd" not in data:
            raise DeviceCommandError("envelope requires 'v' and 'cmd'")
        if data["v"] != PROTOCOL_VERSION:
            raise DeviceCommandError(
                f"unsupported protocol version {data['v']} "
                f"(only {PROTOCOL_VERSION}; no auto-upgrade/downgrade)")
        cmd_dict = data["cmd"]
        if not isinstance(cmd_dict, dict):
            raise DeviceCommandError("'cmd' must be an object")
        # from_dict validates the full closed schema (unknown fields,
        # operation enum, parameters, provenance, ids, version)
        return DeviceCommand.from_dict(cmd_dict)
