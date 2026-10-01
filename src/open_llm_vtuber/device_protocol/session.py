"""Device session layer: identity, heartbeat, capability advertisement,
and the command gate (Phase 17).

The server-side answer to four questions:
- WHO is this device (stable identity, no addresses as business ids)
- Is it ONLINE right now (session lifecycle: CONNECTING -> ONLINE ->
  STALE -> DISCONNECTED; REJECTED for invalid HELLOs)
- What does it ACTUALLY support (device advertisement — separate from
  the server's CapabilityContract: both must intersect for execution)
- May a command be sent to it NOW (the command gate: identity ∩
  session ONLINE ∩ protocol match ∩ advertised capability — a NEW
  boundary that never replaces ExecutionPolicy/Gateway; both stand)

Boundary rules (spec):
- Sessions are TRANSPORT-layer state, not AI state: never persisted
  to StorageProvider/Hermes/LTM, never triggering business actions
  (heartbeat is not an AI event; online != authorized).
- A device advertisement can never CREATE or upgrade server
  capabilities — execution requires server-contract ∩ device-ad ∩
  policy ∩ protocol, all of them.
- Timeouts are centralized constants; state transitions accept an
  injectable clock for deterministic testing (no scattered magic
  numbers, no sleeps).
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .command import DeviceCommandError, PROTOCOL_VERSION

# ---- centralized timing constants (no scattered magic numbers) ----
HEARTBEAT_TIMEOUT_S = 30.0    # no heartbeat within this -> ONLINE->STALE
STALE_TIMEOUT_S = 90.0        # stale for this long -> STALE->DISCONNECTED

# ---- session states ----
SESSION_CONNECTING = "CONNECTING"
SESSION_ONLINE = "ONLINE"
SESSION_STALE = "STALE"
SESSION_DISCONNECTED = "DISCONNECTED"
SESSION_REJECTED = "REJECTED"

# ---- command-gate rejection codes (machine-readable) ----
GATE_OK = "OK"
DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
DEVICE_OFFLINE = "DEVICE_OFFLINE"
DEVICE_STALE = "DEVICE_STALE"
DEVICE_PROTOCOL_MISMATCH = "DEVICE_PROTOCOL_MISMATCH"
DEVICE_CAPABILITY_UNSUPPORTED = "DEVICE_CAPABILITY_UNSUPPORTED"

# ---- protocol message types (Phase 17 additions to the matrix) ----
DEVICE_MESSAGE_TYPES = ("device_hello", "heartbeat", "heartbeat_ack",
                        "capability_advertisement")


class DeviceSessionError(ValueError):
    """Raised on any session-layer schema violation (fail closed)."""


# ---------------------------------------------------------------------------
# Protocol messages (closed schemas, from_dict rejects unknown keys)
# ---------------------------------------------------------------------------

@dataclass
class DeviceHello:
    """ESP32 -> Server on connect. Creates a session (or is REJECTED)."""
    message_type: str = "device_hello"
    protocol_version: int = PROTOCOL_VERSION
    device_id: str = ""
    device_type: str = ""
    firmware_version: str = ""

    def validate(self) -> None:
        if self.message_type != "device_hello":
            raise DeviceSessionError(
                f"message_type must be 'device_hello' (got "
                f"'{self.message_type}')")
        if self.protocol_version != PROTOCOL_VERSION:
            raise DeviceSessionError(
                f"unsupported protocol_version {self.protocol_version} "
                f"(only {PROTOCOL_VERSION}; no best-effort accept)")
        if not self.device_id or not isinstance(self.device_id, str):
            raise DeviceSessionError("device_id must be a non-empty string")
        # business identity: addresses are transport-layer, never ids
        low = self.device_id.lower()
        for marker in ("/", ":", "@", "http", "tcp", "udp", ".local",
                       "192.168.", "10.0.", "127.0"):
            if marker in low:
                raise DeviceSessionError(
                    f"device_id '{self.device_id}' looks like a transport "
                    f"address — business identity required")
        if not self.device_type:
            raise DeviceSessionError("device_type is required")
        if not self.firmware_version:
            raise DeviceSessionError("firmware_version is required")

    def to_dict(self) -> Dict[str, Any]:
        return {"message_type": self.message_type,
                "protocol_version": self.protocol_version,
                "device_id": self.device_id,
                "device_type": self.device_type,
                "firmware_version": self.firmware_version}

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "DeviceHello":
        allowed = {"message_type", "protocol_version", "device_id",
                   "device_type", "firmware_version"}
        unknown = set(d.keys()) - allowed
        if unknown:
            raise DeviceSessionError(
                f"unknown DeviceHello fields {sorted(unknown)} (closed "
                f"schema: {sorted(allowed)})")
        hello = DeviceHello(
            message_type=str(d.get("message_type", "")),
            protocol_version=int(d.get("protocol_version", 0)),
            device_id=str(d.get("device_id", "")),
            device_type=str(d.get("device_type", "")),
            firmware_version=str(d.get("firmware_version", "")))
        hello.validate()
        return hello


@dataclass
class Heartbeat:
    """ESP32 -> Server, transport liveness only (carries NO AI state)."""
    message_type: str = "heartbeat"
    protocol_version: int = PROTOCOL_VERSION
    device_id: str = ""

    def validate(self) -> None:
        if self.message_type != "heartbeat":
            raise DeviceSessionError(
                f"message_type must be 'heartbeat'")
        if self.protocol_version != PROTOCOL_VERSION:
            raise DeviceSessionError(
                f"unsupported protocol_version {self.protocol_version}")
        if not self.device_id:
            raise DeviceSessionError("device_id is required")

    def to_dict(self) -> Dict[str, Any]:
        return {"message_type": self.message_type,
                "protocol_version": self.protocol_version,
                "device_id": self.device_id}

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Heartbeat":
        allowed = {"message_type", "protocol_version", "device_id"}
        unknown = set(d.keys()) - allowed
        if unknown:
            raise DeviceSessionError(
                f"unknown Heartbeat fields {sorted(unknown)}")
        hb = Heartbeat(
            message_type=str(d.get("message_type", "")),
            protocol_version=int(d.get("protocol_version", 0)),
            device_id=str(d.get("device_id", "")))
        hb.validate()
        return hb


@dataclass
class CapabilityAdvertisement:
    """ESP32 -> Server: which protocol OPERATIONS this device declares.

    An advertisement can never create or upgrade server capabilities —
    it only intersects with them at the command gate.
    """
    message_type: str = "capability_advertisement"
    protocol_version: int = PROTOCOL_VERSION
    device_id: str = ""
    operations: List[str] = field(default_factory=list)

    def validate(self) -> None:
        if self.message_type != "capability_advertisement":
            raise DeviceSessionError(
                f"message_type must be 'capability_advertisement'")
        if self.protocol_version != PROTOCOL_VERSION:
            raise DeviceSessionError(
                f"unsupported protocol_version {self.protocol_version}")
        if not self.device_id:
            raise DeviceSessionError("device_id is required")
        if not isinstance(self.operations, list):
            raise DeviceSessionError("operations must be a list")
        for op in self.operations:
            if not isinstance(op, str) or not op:
                raise DeviceSessionError(
                    "each advertised operation must be a non-empty string")

    def to_dict(self) -> Dict[str, Any]:
        return {"message_type": self.message_type,
                "protocol_version": self.protocol_version,
                "device_id": self.device_id,
                "operations": list(self.operations)}

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "CapabilityAdvertisement":
        allowed = {"message_type", "protocol_version", "device_id",
                   "operations"}
        unknown = set(d.keys()) - allowed
        if unknown:
            raise DeviceSessionError(
                f"unknown CapabilityAdvertisement fields {sorted(unknown)}")
        adv = CapabilityAdvertisement(
            message_type=str(d.get("message_type", "")),
            protocol_version=int(d.get("protocol_version", 0)),
            device_id=str(d.get("device_id", "")),
            operations=list(d.get("operations") or []))
        adv.validate()
        return adv


def build_heartbeat_ack(device_id: str,
                        protocol_version: int = PROTOCOL_VERSION) -> Dict[str, Any]:
    """Server -> ESP32 heartbeat acknowledgement (typed envelope)."""
    return {"message_type": "heartbeat_ack",
            "protocol_version": protocol_version,
            "device_id": device_id}


# ---------------------------------------------------------------------------
# DeviceSession + Registry/Manager (in-memory runtime state only)
# ---------------------------------------------------------------------------

@dataclass
class DeviceSession:
    session_id: str
    device_id: str
    device_type: str
    protocol_version: int
    firmware_version: str
    capabilities: List[str] = field(default_factory=list)  # advertised ops
    connected_at: float = 0.0
    last_seen: float = 0.0
    state: str = SESSION_CONNECTING

    def to_dict(self) -> Dict[str, Any]:
        return {"session_id": self.session_id, "device_id": self.device_id,
                "device_type": self.device_type,
                "protocol_version": self.protocol_version,
                "firmware_version": self.firmware_version,
                "capabilities": list(self.capabilities),
                "connected_at": self.connected_at,
                "last_seen": self.last_seen, "state": self.state}


class DeviceRegistry:
    """In-memory device_id -> session registry (RUNTIME state only —
    never persisted to StorageProvider/Hermes/LTM; sessions are
    transport-layer lifecycle, not AI memory).

    A device_id has AT MOST ONE live session: reconnect marks the old
    session DISCONNECTED and creates a NEW session_id (never reused).
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, DeviceSession] = {}

    # -- hello (connect) --------------------------------------------------------

    def register_hello(self, hello: DeviceHello,
                       now: Optional[float] = None) -> DeviceSession:
        """Validate + create an ONLINE session. A previous session for the
        same device_id is DISCONNECTED (new session_id, never reused)."""
        hello.validate()
        now = time.time() if now is None else now
        old = self._sessions.get(hello.device_id)
        if old is not None:
            old.state = SESSION_DISCONNECTED   # old session invalidated
        session = DeviceSession(
            session_id=uuid.uuid4().hex[:16],
            device_id=hello.device_id,
            device_type=hello.device_type,
            protocol_version=hello.protocol_version,
            firmware_version=hello.firmware_version,
            connected_at=now, last_seen=now, state=SESSION_ONLINE)
        self._sessions[hello.device_id] = session
        return session

    def reject_hello(self, hello: DeviceHello) -> DeviceSession:
        """Record a REJECTED hello attempt (no session created; the
        returned stub is bookkeeping for the caller only)."""
        hello.validate()   # raises on schema errors
        return DeviceSession(
            session_id=uuid.uuid4().hex[:16], device_id=hello.device_id,
            device_type=hello.device_type,
            protocol_version=hello.protocol_version,
            firmware_version=hello.firmware_version,
            state=SESSION_REJECTED)

    # -- lookup / heartbeat / advertisement ----------------------------------------

    def get(self, device_id: str) -> Optional[DeviceSession]:
        return self._sessions.get(device_id)

    def heartbeat(self, hb: Heartbeat,
                  now: Optional[float] = None) -> Optional[DeviceSession]:
        """Update last_seen on an ONLINE/STALE session (returns it), or
        None when the device is unknown (fail closed)."""
        hb.validate()
        now = time.time() if now is None else now
        session = self._sessions.get(hb.device_id)
        if session is None:
            return None
        if session.state in (SESSION_ONLINE, SESSION_STALE):
            session.last_seen = now
            session.state = SESSION_ONLINE   # STALE revives on heartbeat
        return session

    def advertise(self, adv: CapabilityAdvertisement,
                  now: Optional[float] = None) -> Optional[DeviceSession]:
        """Record a device's advertised operations on its live session.
        An advertisement NEVER creates or upgrades server capabilities."""
        adv.validate()
        now = time.time() if now is None else now
        session = self._sessions.get(adv.device_id)
        if session is None or session.state not in (SESSION_ONLINE,
                                                     SESSION_STALE):
            return None
        session.capabilities = list(adv.operations)
        session.last_seen = now
        return session

    def disconnect(self, device_id: str) -> bool:
        session = self._sessions.get(device_id)
        if session is None:
            return False
        session.state = SESSION_DISCONNECTED
        return True

    def remove(self, device_id: str) -> bool:
        return self._sessions.pop(device_id, None) is not None

    def list_devices(self) -> List[str]:
        return sorted(self._sessions.keys())

    # -- lifecycle ticking (injectable clock; no sleeps) -----------------------------

    def check_stale(self, now: Optional[float] = None) -> List[DeviceSession]:
        """ONLINE --(no heartbeat for HEARTBEAT_TIMEOUT_S)--> STALE
        STALE --(last_seen older than STALE_TIMEOUT_S)--> DISCONNECTED.
        Returns the sessions whose state changed."""
        now = time.time() if now is None else now
        changed: List[DeviceSession] = []
        for session in self._sessions.values():
            if session.state == SESSION_ONLINE \
                    and now - session.last_seen > HEARTBEAT_TIMEOUT_S:
                session.state = SESSION_STALE
                changed.append(session)
            elif session.state == SESSION_STALE \
                    and now - session.last_seen > STALE_TIMEOUT_S:
                session.state = SESSION_DISCONNECTED
                changed.append(session)
        return changed

    # -- THE COMMAND GATE (Phase-17 core boundary) -------------------------------------

    def send_allowed(self, device_id: str, operation: str,
                     protocol_version: int = PROTOCOL_VERSION,
                     now: Optional[float] = None) -> Tuple[bool, str]:
        """May a DeviceCommand be sent to this device NOW?

        Checks (all must pass, in order): device exists (ONLINE state
        required — STALE/DISCONNECTED rejected), protocol version match,
        operation advertised by the device. Returns (ok, machine code).
        This gate NEVER replaces ExecutionPolicy/Gateway — both stand.
        """
        self.check_stale(now)   # refresh lifecycle before deciding
        session = self._sessions.get(device_id)
        if session is None:
            return False, DEVICE_NOT_FOUND
        if session.state == SESSION_DISCONNECTED:
            return False, DEVICE_OFFLINE
        if session.state == SESSION_STALE:
            return False, DEVICE_STALE
        if session.state != SESSION_ONLINE:
            return False, DEVICE_OFFLINE
        if session.protocol_version != protocol_version:
            return False, DEVICE_PROTOCOL_MISMATCH
        if operation not in session.capabilities:
            return False, DEVICE_CAPABILITY_UNSUPPORTED
        return True, GATE_OK
