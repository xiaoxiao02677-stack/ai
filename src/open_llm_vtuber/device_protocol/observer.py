"""Device observer layer: read-only state / health / command lifecycle
snapshots (Phase 18).

Everything here is DERIVED from the Phase-17 authoritative objects —
DeviceState and DeviceHealth are SNAPSHOTS of DeviceSession, never a
second state machine; device readiness is a derived read of the
command gate (send_allowed), never a bypass; the command lifecycle
mirrors what the Phase-16 adapter/DeviceAck actually did, never
decides what should happen next.

Boundaries (spec):
- Observer is read-only w.r.t. the execution chain: no send/execute/
  retry/queue/schedule. Observes events, builds snapshots, nothing
  more.
- Command history is BOUNDED in-memory FIFO (MAX_COMMAND_HISTORY=64),
  deterministic, records only command_id/device_id/operation/timing/
  status/error_code — NEVER parameters, prompts, conversations,
  memory or privacy text.
- Zero persistence (no DB tables), zero new protocol messages
  (HELLO/HEARTBEAT/ADVERTISEMENT/ACK already express everything),
  zero business actions.
- ACK integrity: an ack must reference a KNOWN command (unknown ->
  ignored, no history entry) AND the SAME device_id (forged ->
  ignored); duplicate acks cannot re-complete a finished lifecycle
  (exactly-once logical completion); a late ack after TIMEOUT never
  rewrites the finished result (recorded as an observability flag
  only).
"""

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .ack import DeviceAck
from .session import (DeviceRegistry, SESSION_ONLINE, SESSION_STALE,
                      SESSION_DISCONNECTED, GATE_OK)

# bounded in-memory history (spec §15/§16 — no persistence)
MAX_COMMAND_HISTORY = 64

# command lifecycle (mirrors the Phase-16 adapter semantics; the
# observation layer records what happened, never decides next steps)
LIFECYCLE_CREATED = "CREATED"
LIFECYCLE_SENT = "SENT"
LIFECYCLE_ACKED = "ACKED"
LIFECYCLE_NACKED = "NACKED"
LIFECYCLE_REJECTED = "REJECTED"
LIFECYCLE_TIMEOUT = "TIMEOUT"
LIFECYCLE_FAILED = "FAILED"
_TERMINAL = (LIFECYCLE_ACKED, LIFECYCLE_NACKED, LIFECYCLE_REJECTED,
             LIFECYCLE_TIMEOUT, LIFECYCLE_FAILED)

# health lookup codes (mirroring the session-state vocabulary only —
# no GOOD/BAD/HAPPY-style business states)
HEALTH_NOT_FOUND = "NOT_FOUND"
HEALTH_OK = "OK"


@dataclass
class CommandRecord:
    """One bounded lifecycle observation entry (no parameters/payloads)."""
    command_id: str
    device_id: str
    operation: str
    status: str = LIFECYCLE_CREATED
    error_code: Optional[str] = None
    created_at: float = 0.0
    sent_at: Optional[float] = None
    ack_at: Optional[float] = None
    late_ack: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"command_id": self.command_id, "device_id": self.device_id,
                "operation": self.operation, "status": self.status,
                "error_code": self.error_code,
                "created_at": self.created_at, "sent_at": self.sent_at,
                "ack_at": self.ack_at, "late_ack": self.late_ack}


class CommandHistory:
    """Bounded deterministic FIFO of command lifecycle records."""

    def __init__(self, max_len: int = MAX_COMMAND_HISTORY):
        self.max_len = max_len
        self._records: "OrderedDict[str, CommandRecord]" = OrderedDict()

    def add(self, record: CommandRecord) -> CommandRecord:
        """Insert/replace; evicts the OLDEST entry beyond max_len (FIFO)."""
        self._records[record.command_id] = record
        self._records.move_to_end(record.command_id)
        while len(self._records) > self.max_len:
            self._records.popitem(last=False)   # evict oldest
        return record

    def get(self, command_id: str) -> Optional[CommandRecord]:
        return self._records.get(command_id)

    def __len__(self) -> int:
        return len(self._records)

    def list_records(self) -> List[CommandRecord]:
        return list(self._records.values())


@dataclass
class DeviceState:
    """Read-only snapshot of a DeviceSession (view, not authority)."""
    device_id: str
    session_id: str
    device_type: str
    protocol_version: int
    firmware_version: str
    connection_state: str            # == session.state (authoritative)
    last_seen: float
    capabilities: List[str]
    last_command_id: str = ""
    last_command_status: str = ""
    last_ack_status: str = ""        # ACK / NACK / ""
    updated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"device_id": self.device_id, "session_id": self.session_id,
                "device_type": self.device_type,
                "protocol_version": self.protocol_version,
                "firmware_version": self.firmware_version,
                "connection_state": self.connection_state,
                "last_seen": self.last_seen,
                "capabilities": list(self.capabilities),
                "last_command_id": self.last_command_id,
                "last_command_status": self.last_command_status,
                "last_ack_status": self.last_ack_status,
                "updated_at": self.updated_at}


@dataclass
class DeviceHealth:
    """Computed communication health (derived — no second timestamps).

    States come ONLY from the session vocabulary: ONLINE / STALE /
    DISCONNECTED. No GOOD/BAD/emotion-style business states.
    """
    device_id: str
    health_state: str                 # ONLINE / STALE / DISCONNECTED
    online: bool
    last_seen: float
    heartbeat_age: float              # now - last_seen (computed)
    session_id: str = ""
    protocol_compatible: bool = False
    capability_valid: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"device_id": self.device_id,
                "health_state": self.health_state, "online": self.online,
                "last_seen": self.last_seen,
                "heartbeat_age": self.heartbeat_age,
                "session_id": self.session_id,
                "protocol_compatible": self.protocol_compatible,
                "capability_valid": self.capability_valid}


class DeviceObserver:
    """Read-only observer over sessions + command lifecycle.

    Public API (internal Python — no HTTP server added):
      get_device_state(device_id, now) -> Optional[DeviceState]
      get_device_health(device_id, now) -> (Optional[DeviceHealth], code)
      get_command_status(command_id) -> Optional[CommandRecord]
      device_ready(device_id, operation, now) -> (bool, code)
      observe_* (event recording used by the adapter)
    There is deliberately NO send/execute/retry/queue/schedule here.
    """

    def __init__(self, registry: DeviceRegistry,
                 history: Optional[CommandHistory] = None):
        self.registry = registry
        self.history = history if history is not None else CommandHistory()

    # -- snapshots -----------------------------------------------------------------

    def get_device_state(self, device_id: str,
                         now: Optional[float] = None) -> Optional[DeviceState]:
        """Snapshot from the authoritative session (unknown -> None)."""
        now = time.time() if now is None else now
        session = self.registry.get(device_id)
        if session is None:
            return None
        last = self._last_record_for(device_id)
        return DeviceState(
            device_id=session.device_id, session_id=session.session_id,
            device_type=session.device_type,
            protocol_version=session.protocol_version,
            firmware_version=session.firmware_version,
            connection_state=session.state,
            last_seen=session.last_seen,
            capabilities=list(session.capabilities),
            last_command_id=last.command_id if last else "",
            last_command_status=last.status if last else "",
            last_ack_status=self._ack_status_of(last) if last else "",
            updated_at=now)

    def get_device_health(self, device_id: str,
                          now: Optional[float] = None
                          ) -> Tuple[Optional[DeviceHealth], str]:
        """Computed health from the session vocabulary (fail closed)."""
        now = time.time() if now is None else now
        session = self.registry.get(device_id)
        if session is None:
            return None, HEALTH_NOT_FOUND
        if session.state not in (SESSION_ONLINE, SESSION_STALE,
                                 SESSION_DISCONNECTED):
            return None, HEALTH_NOT_FOUND
        return DeviceHealth(
            device_id=device_id, health_state=session.state,
            online=session.state == SESSION_ONLINE,
            last_seen=session.last_seen,
            heartbeat_age=max(0.0, now - session.last_seen),
            session_id=session.session_id,
            protocol_compatible=session.protocol_version == 1,
            capability_valid=isinstance(session.capabilities, list)
            and all(isinstance(c, str) for c in session.capabilities)), \
            HEALTH_OK

    def get_command_status(self, command_id: str) -> Optional[CommandRecord]:
        return self.history.get(command_id)

    def device_ready(self, device_id: str, operation: str,
                     now: Optional[float] = None) -> Tuple[bool, str]:
        """Derived read of the Phase-17 command gate — readiness is a
        FACT, never a bypass of Policy/Gateway."""
        return self.registry.send_allowed(device_id, operation, now=now)

    # -- lifecycle observation (event recording, adapter-driven) -----------------------

    def observe_command_created(self, command_id: str, device_id: str,
                                operation: str,
                                now: Optional[float] = None) -> CommandRecord:
        now = time.time() if now is None else now
        return self.history.add(CommandRecord(
            command_id=command_id, device_id=device_id, operation=operation,
            status=LIFECYCLE_CREATED, created_at=now))

    def observe_command_sent(self, command_id: str,
                             now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        rec = self.history.get(command_id)
        if rec is None or rec.status in _TERMINAL:
            return
        rec.status = LIFECYCLE_SENT
        rec.sent_at = now

    def observe_terminal(self, command_id: str, status: str,
                         error_code: Optional[str] = None,
                         now: Optional[float] = None) -> None:
        """Record REJECTED/TIMEOUT/FAILED terminal outcomes (the adapter
        never records an ACK here — observe_ack owns ack integrity)."""
        now = time.time() if now is None else now
        if status not in (LIFECYCLE_REJECTED, LIFECYCLE_TIMEOUT,
                          LIFECYCLE_FAILED):
            raise ValueError(f"observe_terminal: '{status}' is not terminal")
        rec = self.history.get(command_id)
        if rec is None:
            return
        rec.status = status
        rec.error_code = error_code
        rec.ack_at = now

    def observe_ack(self, ack: DeviceAck,
                    now: Optional[float] = None) -> bool:
        """Consume a DeviceAck with full integrity checks.

        - unknown command_id -> ignored (False; NO history entry created)
        - device_id mismatch with the command's device -> ignored (False)
        - already terminal -> duplicate/late: NEVER re-completes; a late
          ack after TIMEOUT only sets the late_ack observability flag
        - first completion -> ACKED / NACKED, exactly-once
        """
        now = time.time() if now is None else now
        rec = self.history.get(ack.command_id)
        if rec is None:
            return False                      # unknown ack: ignored
        if rec.device_id != ack.device_id:
            return False                      # device forgery: ignored
        if rec.status in _TERMINAL:
            # duplicate or late ack: never rewrites a finished result
            if rec.status in (LIFECYCLE_TIMEOUT, LIFECYCLE_FAILED):
                rec.late_ack = True           # observability flag only
            return False
        if ack.status == "ACK":
            rec.status = LIFECYCLE_ACKED
            rec.error_code = None
        else:
            rec.status = LIFECYCLE_NACKED
            rec.error_code = ack.error_code
        rec.ack_at = now
        return True

    # -- internals ---------------------------------------------------------------------

    def _last_record_for(self, device_id: str) -> Optional[CommandRecord]:
        last: Optional[CommandRecord] = None
        for rec in self.history.list_records():
            if rec.device_id == device_id:
                last = rec
        return last

    @staticmethod
    def _ack_status_of(rec: Optional[CommandRecord]) -> str:
        if rec is None:
            return ""
        if rec.status == LIFECYCLE_ACKED:
            return "ACK"
        if rec.status == LIFECYCLE_NACKED:
            return "NACK"
        return ""
