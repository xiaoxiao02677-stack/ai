"""Transport: the message-delivery boundary (interface + MockTransport).

Phase 15 implements ONLY the abstraction and an in-memory mock —
deterministic, network-free, device-free. Real transports (MQTT/HTTP/
BLE/Serial) are future phases and MUST implement this same interface
behind the same Gateway/Policy boundary.

Semantics (per Phase 14): transport delivery != device execution.
MockTransport models the four-state boundary minimally:
  - send() accepts a message under a message_id (duplicate ids are
    REJECTED — no silent overwrite)
  - a PRE-PROGRAMMED response can be staged per message_id (what the
    mock "device" would answer); without one, receive() times out
    unless a default response is set
  - malformed staged responses surface as decode errors upstream —
    the transport carries bytes/strings, never interprets semantics
"""

from typing import Dict, Optional


class TransportTimeout(Exception):
    """Raised when a receive() finds no response within its scope."""


class TransportError(Exception):
    """Raised on transport-level violations (duplicate ids, etc.)."""


class Transport:
    """Minimal transport interface (message delivery boundary)."""

    def send(self, message_id: str, message: str,
             timeout: float = 1.0) -> None:
        raise NotImplementedError

    def receive(self, message_id: str,
                timeout: float = 1.0) -> Optional[str]:
        raise NotImplementedError


class MockTransport(Transport):
    """Deterministic in-memory transport (send stores, receive reads).

    No network, no devices, no threads, no clocks — 'timeout' here
    means 'no staged response under this message_id'.
    """

    def __init__(self) -> None:
        self._sent: Dict[str, str] = {}
        self._responses: Dict[str, str] = {}
        self._default_response: Optional[str] = None

    # -- send side -----------------------------------------------------------

    def send(self, message_id: str, message: str,
             timeout: float = 1.0) -> None:
        if not message_id or not isinstance(message_id, str):
            raise TransportError("message_id must be a non-empty string")
        if not isinstance(message, str):
            raise TransportError("message must be a string")
        if message_id in self._sent:
            # duplicates rejected — idempotency is a device concern,
            # the transport refuses silent overwrite of history
            raise TransportError(
                f"duplicate message_id '{message_id}' — refused")
        self._sent[message_id] = message

    # -- receive side ------------------------------------------------------------

    def receive(self, message_id: str,
                timeout: float = 1.0) -> Optional[str]:
        resp = self._responses.get(message_id)
        if resp is not None:
            return resp
        if self._default_response is not None:
            return self._default_response
        raise TransportTimeout(
            f"no response staged for message_id '{message_id}'")

    # -- mock programming (test/audit use) ------------------------------------------

    def stage_response(self, message_id: str, response: str) -> None:
        self._responses[message_id] = response

    def stage_default_response(self, response: Optional[str]) -> None:
        self._default_response = response

    def sent_messages(self) -> Dict[str, str]:
        return dict(self._sent)

    def was_sent(self, message_id: str) -> bool:
        return message_id in self._sent
