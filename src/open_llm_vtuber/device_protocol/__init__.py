"""Device Protocol (Phase 15): typed device commands + codec + transport mock.

The Device Protocol LAYER — strictly separate from the AI/decision
layer (ActionIntent et al.) and from the execution boundary
(Gateway/Policy/Adapters live in execution/). Contains:

- command.DeviceCommand — closed-schema, typed, bounded server->device
  command with a deterministic idempotency key (command_id) and full
  provenance; operation is a controlled enum (TEST_ECHO only in
  Phase 15, explicitly TEST/MOCK ONLY).
- protocol.DeviceProtocol — deterministic encode/decode with total
  validation (schema/version/operation/parameters/provenance); any
  flaw raises, nothing is auto-repaired.
- transport.Transport / MockTransport — the message-delivery boundary
  abstraction and its in-memory implementation (network-free,
  device-free; real transports are future phases behind the same
  interface).

Boundaries: zero network, zero devices, zero side effects. The
ESP32Adapter (in execution/) composes these pieces; this package
knows nothing about AI concepts (Reflection/Decision/Strategy...).
"""

from .command import (DeviceCommand, DeviceCommandError,
                      derive_command_id, DEVICE_OPERATIONS,
                      PROTOCOL_VERSION)
from .protocol import DeviceProtocol
from .transport import Transport, MockTransport, TransportTimeout, \
    TransportError
from .real_transport import RealTCPTransport
from .ack import DeviceAck, DeviceAckError, ACK_STATUS, ACK_ERROR_CODES

__all__ = [
    "DeviceCommand",
    "DeviceCommandError",
    "derive_command_id",
    "DEVICE_OPERATIONS",
    "PROTOCOL_VERSION",
    "DeviceProtocol",
    "Transport",
    "MockTransport",
    "TransportTimeout",
    "TransportError",
    "RealTCPTransport",
    "DeviceAck", "DeviceAckError", "ACK_STATUS", "ACK_ERROR_CODES",
]
