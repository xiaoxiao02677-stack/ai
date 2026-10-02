"""P19-R real-device validation SERVICE (single protocol implementation).

Extracted from tools/p19r_console.py per the workshop spec §13: the CLI
and the Web UI are two ENTRY POINTS over ONE protocol implementation.
This module owns:

  - XiaozhiCodec: wire codec (flat Xiaozhi frames <-> project protocol
    objects; closed key-set validation before the core schema validates
    again — defense in depth; legacy P15 envelope also accepted)
  - DeviceConnection: newline-framed TCP frame reader/writer
  - validate_device(): the FULL acceptance sequence as a generator of
    step events (hello/advertisement/session/gate/six failure probes/
    SET_LED on-off/duplicate/late-ack/kill-switch), so any front end
    (CLI printer, workshop API, future WebSocket) can consume the same
    stream without a second protocol implementation.

Device Protocol layer stays READ-ONLY + validation-only: no AI imports,
no persistence, no gateway bypass — LED commands go through the
project DeviceCommand/DeviceAck objects, identical semantics to the
P19-R console.
"""
import json
import os
import re
import socket
import sys
import time
import uuid


# import strategy: the server process uses the src. package prefix;
# standalone tools import the bare package (path injected from the
# service file location: src/open_llm_vtuber/device_protocol/ -> src/)
try:
    from src.open_llm_vtuber.device_protocol import (  # noqa: E402
        DeviceCommand, DeviceSessionError, DeviceRegistry, DeviceObserver,
        DeviceHello, CapabilityAdvertisement, DeviceAck,
        SESSION_ONLINE, GATE_OK)
    from src.open_llm_vtuber.device_protocol.observer import (
        LIFECYCLE_ACKED, LIFECYCLE_TIMEOUT)  # noqa: E402
    from src.open_llm_vtuber.execution.policy import (
        GLOBAL_EXECUTION_ENABLED)  # noqa: E402
except ImportError:
    _HERE = os.path.dirname(os.path.abspath(__file__))
    _SRC = os.path.abspath(os.path.join(_HERE, "..", ".."))
    if os.path.isdir(os.path.join(_SRC, "open_llm_vtuber")) \
            and _SRC not in sys.path:
        sys.path.insert(0, _SRC)
    from open_llm_vtuber.device_protocol import (  # noqa: E402
        DeviceCommand, DeviceSessionError, DeviceRegistry, DeviceObserver,
        DeviceHello, CapabilityAdvertisement, DeviceAck,
        SESSION_ONLINE, GATE_OK)
    from open_llm_vtuber.device_protocol.observer import (
        LIFECYCLE_ACKED, LIFECYCLE_TIMEOUT)  # noqa: E402
    from open_llm_vtuber.execution.policy import (
        GLOBAL_EXECUTION_ENABLED)  # noqa: E402


# Xiaozhi wire codec (flat JSON) <-> project protocol objects
# ---------------------------------------------------------------------------

class XiaozhiCodec:
    """Wire adapter: Xiaozhi flat frames <-> project objects.

    Per spec §15 the console adapts the wire format; the project's
    DeviceCommand/DeviceAck/schemas remain the authority (every frame
    is parsed THROUGH them before being trusted).
    """

    # ---- device -> server ------------------------------------------------

    @staticmethod
    def parse_hello(doc):
        """Xiaozhi HELLO {"type":"HELLO","protocol_version":1,
        "device_id":..,"firmware":..,"session_id":..} -> (DeviceHello,
        wire_session_id or None). Raises DeviceSessionError on bad.

        The WIRE frame is validated first (closed key set), then mapped
        into the project object (which validates its own closed schema
        again — defense in depth).
        """
        if not isinstance(doc, dict):
            raise DeviceSessionError("hello frame is not an object")
        if doc.get("type") == "HELLO" or \
                doc.get("message_type") == "device_hello":
            allowed = {"type", "message_type", "protocol_version",
                       "device_id", "device_type", "firmware",
                       "firmware_version", "session_id"}
            unknown = set(doc.keys()) - allowed
            if unknown:
                raise DeviceSessionError(
                    f"unknown HELLO fields {sorted(unknown)} "
                    f"(closed schema)")
            payload = {
                "message_type": "device_hello",
                "protocol_version": doc.get("protocol_version", 0),
                "device_id": doc.get("device_id", ""),
                # Xiaozhi uses 'firmware'; legacy uses 'firmware_version'
                "device_type": doc.get("device_type", "xiaozhi.esp32s3"),
                "firmware_version": doc.get(
                    "firmware_version",
                    doc.get("firmware", "unknown")),
            }
            hello = DeviceHello.from_dict(payload)
            wire_session = doc.get("session_id")   # device-side session
            return hello, wire_session
        raise DeviceSessionError(f"not a hello frame: type="
                                 f"{doc.get('type')!r}")

    @staticmethod
    def parse_advertisement(doc, device_id):
        """Xiaozhi ADVERTISEMENT -> CapabilityAdvertisement.

        The WIRE frame is validated first (closed key set — unknown
        fields are rejected, never silently dropped), then mapped into
        the project object.
        """
        if doc.get("type") == "ADVERTISEMENT" or \
                doc.get("message_type") == "capability_advertisement":
            allowed = {"type", "message_type", "protocol_version",
                       "device_id", "operations"}
            unknown = set(doc.keys()) - allowed
            if unknown:
                raise DeviceSessionError(
                    f"unknown ADVERTISEMENT fields {sorted(unknown)} "
                    f"(closed schema)")
            return CapabilityAdvertisement.from_dict({
                "message_type": "capability_advertisement",
                "protocol_version": doc.get("protocol_version", 0),
                "device_id": doc.get("device_id", device_id),
                "operations": doc.get("operations", []),
            })
        raise DeviceSessionError(
            f"not an advertisement frame: type={doc.get('type')!r}")

    @staticmethod
    def parse_ack(doc):
        """Wire ACK/NACK (flat Xiaozhi or legacy envelope ack) ->
        DeviceAck (validated through the closed schema)."""
        t = doc.get("type", "")
        if t in ("ACK", "NACK") or doc.get("message_type") == "ack":
            status = t if t in ("ACK", "NACK") \
                else doc.get("status", "")
            # Xiaozhi firmware may send a placeholder error_code on ACK
            # frames ("none"/""/null); the P15 closed schema forbids any
            # error_code on ACK — normalize void placeholders to None so
            # a REAL device's ACK parses (the semantic content is the
            # ACK status itself). Real NACK keeps its error_code.
            error_code = doc.get("error_code")
            if status == "ACK" and error_code is not None:
                ec = str(error_code).strip()
                if ec.lower() in ("", "none", "null"):
                    # void placeholder on a genuine ACK — drop it
                    error_code = None
                else:
                    # Xiaozhi firmware expresses NACK semantics with
                    # status=ACK + a real error_code (observed live).
                    # The error_code is the semantic authority -> parse
                    # as NACK. The P15 object stays strict (closed
                    # schema unchanged); this is WIRE normalization in
                    # the codec, exactly its purpose.
                    status = "NACK"
            # firmware error-code vocabulary -> P15 closed enum
            _FIRMWARE_CODE_MAP = {
                "WRONG_DEVICE": "UNKNOWN_DEVICE",
                "UNKNOWN_COMMAND": "UNKNOWN_OPERATION",
                "BAD_PARAMETERS": "INVALID_PARAMETERS",
                "INVALID_PARAMS": "INVALID_PARAMETERS",
                "BAD_VERSION": "UNSUPPORTED_VERSION",
                "PARSE_ERROR": "MALFORMED_MESSAGE",
            }
            if isinstance(error_code, str):
                error_code = _FIRMWARE_CODE_MAP.get(
                    error_code.strip().upper(), error_code)
                if error_code not in (
                        "UNKNOWN_COMMAND", "DUPLICATE_COMMAND",
                        "UNKNOWN_DEVICE", "UNKNOWN_OPERATION",
                        "INVALID_PARAMETERS", "INVALID_SCHEMA",
                        "UNSUPPORTED_VERSION", "MALFORMED_MESSAGE",
                        "TIMEOUT"):
                    # unmappable firmware code -> closed-enum fallback
                    error_code = "INVALID_SCHEMA"
            return DeviceAck.from_dict({
                "command_id": doc.get("command_id", ""),
                "device_id": doc.get("device_id", ""),
                "status": status,
                "error_code": error_code,
                "protocol_version": doc.get("protocol_version", 1),
                "message_type": "ack",
                "echo": doc.get("echo", {}),
                "created_at": doc.get("created_at", time.time()),
            })
        return None   # not an ack frame (hello/heartbeat/adv)

    # ---- server -> device --------------------------------------------------

    @staticmethod
    def encode_command(command):
        """Project DeviceCommand -> Xiaozhi flat COMMAND frame."""
        d = command.to_dict()
        return {
            "type": "COMMAND",
            "protocol_version": d["protocol_version"],
            "command_id": d["command_id"],
            "device_id": d["device_id"],
            "operation": d["operation"],
            "parameters": d.get("parameters", {}),
        }

    @staticmethod
    def encode_heartbeat_ack(device_id):
        return {"type": "HEARTBEAT_ACK", "protocol_version": 1,
                "device_id": device_id}


# ---------------------------------------------------------------------------
# TCP frame helpers
# ---------------------------------------------------------------------------



class DeviceConnection:
    def __init__(self, host, port, timeout):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buf = b""

    @classmethod
    def wrap(cls, sock):
        """Adopt an ALREADY-CONNECTED socket (server-side gateway use —
        no outbound connect is performed)."""
        obj = cls.__new__(cls)
        obj.sock = sock
        obj.buf = b""
        return obj

    def send(self, obj_or_bytes):
        data = obj_or_bytes if isinstance(obj_or_bytes, bytes) \
            else (json.dumps(obj_or_bytes, ensure_ascii=False)
                  + "\n").encode()
        if isinstance(obj_or_bytes, bytes) and not obj_or_bytes.endswith(
                b"\n"):
            data += b"\n"
        self.sock.sendall(data)

    def read_frames(self, idle_wait, max_frames, hard_deadline):
        """Read newline JSON frames until idle or deadline."""
        frames = []
        deadline = time.time() + hard_deadline
        while len(frames) < max_frames and time.time() < deadline:
            self.sock.settimeout(max(0.2, deadline - time.time()))
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                self.buf += chunk
            except socket.timeout:
                if frames:
                    break
                continue
            while b"\n" in self.buf:
                line, _, self.buf = self.buf.partition(b"\n")
                line = line.strip()
                if not line:
                    continue
                try:
                    frames.append(json.loads(line))
                except json.JSONDecodeError:
                    frames.append({"__malformed__": True,
                                   "__raw__": line[:120]
                                   .decode("utf-8", "replace")})
        return frames

    def wait_ack(self, timeout):
        """Read frames until an ACK/NACK frame or timeout -> DeviceAck
        or None. Non-ack frames are skipped."""
        frames = self.read_frames(idle_wait=0, max_frames=8,
                                  hard_deadline=timeout)
        for doc in frames:
            ack = XiaozhiCodec.parse_ack(doc)
            if ack is not None:
                return ack
        return None

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Validation sequence (single implementation, generator of step events)
# ---------------------------------------------------------------------------


def _validate_body(conn, timeout, hello, wire_session_id, advertised,
                   set_led_ok, expected_device_id=None):
    """Sections 3-9 of the acceptance sequence, shared by the outbound
    and the inbound-channel entry paths (single implementation)."""
    device_id = hello.device_id
    adv = None
    if advertised:
        adv = CapabilityAdvertisement(device_id=device_id,
                                      operations=list(advertised))
    # 3. Session + gate
    yield {"kind": "section",
           "title": "3. Session + Device Gate (P17)"}
    registry = DeviceRegistry()
    observer = DeviceObserver(registry)
    session = registry.register_hello(hello, now=time.time())
    yield {"kind": "check", "name": "session ONLINE (P17)",
           "ok": session.state == SESSION_ONLINE}
    if adv:
        registry.advertise(adv, now=time.time())
    gate = registry.send_allowed(device_id, "SET_LED",
                                 now=time.time())
    yield {"kind": "check",
           "name": "Device Gate: ONLINE + SET_LED -> OK",
           "ok": gate == (True, GATE_OK)}
    state = observer.get_device_state(device_id)
    yield {"kind": "device", "device_id": device_id,
           "state": {
               "device_id": state.device_id,
               "session_id": state.session_id,
               "protocol_version": state.protocol_version,
               "connection_state": state.connection_state,
               "last_seen": state.last_seen,
               "capabilities": state.capabilities,
           }}

    # 4. failure probes
    yield {"kind": "section",
           "title": "4. Security failure probes (six, NACK)"}

    def _probe(label, command_id, device, operation, params,
               expect_code, wire_override=None):
        cmd = DeviceCommand(
            command_id=command_id, device_id=device,
            capability="capability.led"
            if operation == "SET_LED" else "capability.test",
            operation=operation, parameters=params,
            protocol_version=1,
            provenance={"action_id": "p19r", "decision_id": "p19r",
                        "evaluation_id": "p19r", "strategy_id": "p19r"},
            created_at=time.time())
        try:
            cmd.validate()
        except Exception as e:
            yield {"kind": "check", "name": label, "ok": True,
                   "detail": f"rejected server-side: "
                             f"{type(e).__name__}"}
            return None
        observer.observe_command_created(command_id, device,
                                         operation, now=time.time())
        frame = wire_override if wire_override is not None \
            else XiaozhiCodec.encode_command(cmd)
        conn.send(frame)
        ack = conn.wait_ack(timeout)
        ok_ack = (ack is not None and ack.status == "NACK"
                  and ack.error_code == expect_code
                  and ack.command_id == command_id)
        yield {"kind": "check", "name": label, "ok": ok_ack,
               "detail": f"got status={getattr(ack, 'status', None)} "
                         f"code={getattr(ack, 'error_code', None)}"}
        return ack

    yield from _probe("wrong device_id -> NACK UNKNOWN_DEVICE",
                      "p-wrongdev-" + uuid.uuid4().hex[:8],
                      "esp32-WRONG", "SET_LED", {"on": True},
                      "UNKNOWN_DEVICE")
    yield from _probe("unknown operation -> NACK UNKNOWN_OPERATION",
                      "p-unkop-" + uuid.uuid4().hex[:8], device_id,
                      "SERVO_MOVE", {"on": True}, "UNKNOWN_OPERATION")
    yield from _probe("unsupported capability -> NACK",
                      "p-unscap-" + uuid.uuid4().hex[:8], device_id,
                      "PLAY_AUDIO", {"vol": 5}, "UNKNOWN_OPERATION")
    bad_ver_id = "p-badver-" + uuid.uuid4().hex[:8]
    conn.send({"type": "COMMAND", "protocol_version": 9,
               "command_id": bad_ver_id, "device_id": device_id,
               "operation": "SET_LED", "parameters": {"on": True}})
    ack = conn.wait_ack(timeout)
    yield {"kind": "check",
           "name": "invalid protocol version -> NACK "
                   "UNSUPPORTED_VERSION",
           "ok": (ack is not None and ack.status == "NACK"
                  and ack.error_code == "UNSUPPORTED_VERSION"),
           "detail": f"got {getattr(ack, 'error_code', None)}"}
    yield from _probe("invalid parameters (string on) -> NACK",
                      "p-badpar-" + uuid.uuid4().hex[:8], device_id,
                      "SET_LED", {"on": "true-string"},
                      "INVALID_PARAMETERS")
    yield from _probe("extra parameter -> NACK INVALID_PARAMETERS",
                      "p-badpar2-" + uuid.uuid4().hex[:8], device_id,
                      "SET_LED", {"on": True, "extra": 1},
                      "INVALID_PARAMETERS")
    conn.send(b"{this is not json")
    ack = conn.wait_ack(timeout)
    yield {"kind": "check",
           "name": "malformed JSON -> NACK MALFORMED_MESSAGE",
           "ok": (ack is not None
                  and ack.error_code == "MALFORMED_MESSAGE"),
           "detail": f"got {getattr(ack, 'error_code', None)}"}

    if not set_led_ok:
        yield {"kind": "fatal",
               "reason": "device did not advertise SET_LED — "
                         "intersection rule blocks LED tests"}
        return

    def _led_round(on, section_title, phase_label):
        yield {"kind": "section", "title": section_title}
        cmd = DeviceCommand(
            command_id=f"led-{'on' if on else 'off'}-"
                       f"{uuid.uuid4().hex[:8]}",
            device_id=device_id, capability="capability.led",
            operation="SET_LED", parameters={"on": on},
            protocol_version=1,
            provenance={"action_id": "p19r", "decision_id": "p19r",
                        "evaluation_id": "p19r", "strategy_id": "p19r"},
            created_at=time.time())
        cmd.validate()
        observer.observe_command_created(cmd.command_id, device_id,
                                         "SET_LED", now=time.time())
        conn.send(XiaozhiCodec.encode_command(cmd))
        ack = conn.wait_ack(timeout)
        integrity = (ack is not None and ack.is_success
                     and ack.command_id == cmd.command_id
                     and ack.device_id == device_id
                     and ack.protocol_version == 1)
        yield {"kind": "check",
               "name": f"SET_LED {'on' if on else 'off'} -> ACK "
                       f"(integrity verified)",
               "ok": integrity,
               "detail": f"cmd={getattr(ack, 'command_id', None)} "
                         f"dev={getattr(ack, 'device_id', None)}"}
        if ack is not None:
            observer.observe_ack(ack, now=time.time())
            rec = observer.get_command_status(cmd.command_id)
            yield {"kind": "check", "name": "lifecycle -> ACKED (P18)",
                   "ok": (rec is not None
                          and rec.status == LIFECYCLE_ACKED)}
        physical = yield {"kind": "led_confirm", "phase": phase_label,
                          "command_id": cmd.command_id,
                          "ack_ok": integrity}
        yield {"kind": "check",
               "name": f"physical LED {'ON' if on else 'OFF'} "
                       f"(human confirmation)",
               "ok": physical is True,
               "detail": "not confirmed" if physical is not True
                         else ""}
        return cmd, ack

    cmd_on, ack_on = yield from _led_round(
        True, "5. SET_LED ON (first real body action)", "on")
    cmd_off, ack_off = yield from _led_round(
        False, "6. SET_LED OFF", "off")

    # 7. idempotency
    yield {"kind": "section", "title": "7. Duplicate command"}
    conn.send(XiaozhiCodec.encode_command(cmd_on))
    ack_dup = conn.wait_ack(timeout)
    dup_ok = (ack_dup is not None and ack_dup.status == "NACK"
              and ack_dup.error_code == "DUPLICATE_COMMAND")
    yield {"kind": "check",
           "name": "duplicate command_id -> NACK DUPLICATE_COMMAND",
           "ok": dup_ok,
           "detail": f"got {getattr(ack_dup, 'error_code', None)}"}
    if ack_dup is not None:
        observer.observe_ack(ack_dup, now=time.time())
    rec = observer.get_command_status(cmd_on.command_id)
    yield {"kind": "check",
           "name": "lifecycle stays ACKED (exactly-once)",
           "ok": rec is not None and rec.status == LIFECYCLE_ACKED}

    # 8. late ack
    yield {"kind": "section", "title": "8. Late-ACK (P18)"}
    cmd_late = DeviceCommand(
        command_id="late-" + uuid.uuid4().hex[:8], device_id=device_id,
        capability="capability.led", operation="SET_LED",
        parameters={"on": True}, protocol_version=1,
        provenance={"action_id": "p19r", "decision_id": "p19r",
                    "evaluation_id": "p19r", "strategy_id": "p19r"},
        created_at=time.time())
    cmd_late.validate()
    observer.observe_command_created(cmd_late.command_id, device_id,
                                     "SET_LED", now=time.time())
    observer.observe_command_sent(cmd_late.command_id,
                                  now=time.time())
    conn.send(XiaozhiCodec.encode_command(cmd_late))
    observer.observe_terminal(cmd_late.command_id, LIFECYCLE_TIMEOUT,
                              error_code="TIMEOUT", now=time.time())
    late_ack = conn.wait_ack(timeout + 3.0)
    yield {"kind": "check", "name": "late ACK eventually arrives",
           "ok": (late_ack is not None
                  and late_ack.command_id == cmd_late.command_id)}
    if late_ack is not None:
        observer.observe_ack(late_ack, now=time.time())
    rec = observer.get_command_status(cmd_late.command_id)
    yield {"kind": "check",
           "name": "lifecycle stays TIMEOUT (never rewritten)",
           "ok": rec is not None and rec.status == LIFECYCLE_TIMEOUT}
    yield {"kind": "check", "name": "late_ack flag recorded",
           "ok": rec is not None and rec.late_ack is True}

    # 9. kill switch
    yield {"kind": "section", "title": "9. Kill Switch (code-level)"}
    yield {"kind": "check",
           "name": "GLOBAL_EXECUTION_ENABLED = False",
           "ok": GLOBAL_EXECUTION_ENABLED is False}


def validate_device(host, port=3333, timeout=6.0, expected_device_id=None,
                    src=None, channel=None, known_session=None):
    """Run the FULL P19-R acceptance sequence against a real device.

    channel: optional duck-typed connection (send/read_frames/wait_ack/
    close) — e.g. the workshop gateway's inbound channel for client-mode
    firmware. When None an outbound TCP connection is made to host:port.
    Only a self-made connection is closed in finally().
    known_session: with an inbound channel the HELLO/ADVERTISEMENT were
    already consumed by the gateway at registration time — pass the
    parsed DeviceHello + advertised ops here to skip the read phase
    ({"hello": DeviceHello, "operations": [...]}) without fabricating.

    Yields events (dicts) for the caller to render/collect:
      {"kind": "section", "title": ...}
      {"kind": "check", "name": ..., "ok": bool, "detail": ...}
      {"kind": "info", "text": ...}
      {"kind": "fatal", "reason": ...}          -> sequence aborts after
      {"kind": "verdict", "passed": int, "failed": int}
      {"kind": "device", "device_id": ..., "state": {...}}  (after session)
      {"kind": "led_confirm", "phase": "on"|"off", ...} — caller decides
      {"kind": "executed", "sequence": [...]}   (firmware-side dedup trace
                                                from an optional probe)
    The caller passes the generator's send() value back in to confirm
    physical LED (True/False); the generator yields led_confirm and
    waits — deterministic and interactive-front-end friendly.
    """

    own_connection = channel is None
    conn = channel if channel is not None else DeviceConnection(host, port,
                                                                timeout)
    try:
        if known_session is not None:
            # inbound link: the gateway already consumed HELLO/ADV at
            # registration — reuse the parsed session (no fabrication)
            hello = known_session["hello"]
            wire_session_id = known_session.get("wire_session_id")
            yield {"kind": "section",
                   "title": "1. 连接（入站网关通道，会话已注册）"}
            yield {"kind": "check", "name": "HELLO（网关注册时已验证）",
                   "ok": hello.protocol_version == 1
                   and bool(hello.device_id)}
            hello = hello
            device_id = hello.device_id
            yield {"kind": "info", "text": f"device_id={device_id} "
                                           f"type={hello.device_type} "
                                           f"fw={hello.firmware_version}"}
            if wire_session_id:
                yield {"kind": "info",
                       "text": f"device-side session_id={wire_session_id}"}
            advertised = list(known_session.get("operations", []))
            yield {"kind": "section",
                   "title": "2. ADVERTISEMENT (server ∩ device)"}
            yield {"kind": "check",
                   "name": "ADVERTISEMENT（网关注册时已接收）", "ok": True}
            yield {"kind": "info", "text": f"advertised: {advertised}"}
            set_led_ok = "SET_LED" in advertised
            yield {"kind": "check",
                   "name": "SET_LED advertised BY THE DEVICE (not assumed)",
                   "ok": set_led_ok}
            # jump straight to session/gate (section 3) — bidirectional
            # delegation so led_confirm answers flow back up
            sub = _validate_body(conn, timeout, hello, wire_session_id,
                                 advertised, set_led_ok,
                                 expected_device_id)
            to_sub = None
            try:
                while True:
                    event = sub.send(to_sub)
                    to_sub = None
                    if event["kind"] == "led_confirm":
                        to_sub = (yield event)
                    else:
                        yield event
            except StopIteration:
                pass
            return

        # 1. HELLO (outbound mode)
        yield {"kind": "section", "title": "1. TCP connect + HELLO"}
        frames = conn.read_frames(idle_wait=1.0, max_frames=3,
                                  hard_deadline=timeout)
        hello_doc = next((f for f in frames
                          if isinstance(f, dict)
                          and (f.get("type") == "HELLO"
                               or f.get("message_type") == "device_hello")),
                         None)
        yield {"kind": "check", "name": "HELLO frame received",
               "ok": hello_doc is not None}
        hello = None
        wire_session_id = None
        if hello_doc:
            try:
                hello, wire_session_id = XiaozhiCodec.parse_hello(hello_doc)
                yield {"kind": "check",
                       "name": "HELLO valid (schema + version + identity)",
                       "ok": (hello.protocol_version == 1
                              and bool(hello.device_id))}
            except DeviceSessionError as e:
                yield {"kind": "check", "name": "HELLO valid", "ok": False,
                       "detail": str(e)}
        if hello is None:
            yield {"kind": "fatal", "reason": "no valid HELLO"}
            return

        device_id = hello.device_id
        if expected_device_id and expected_device_id != device_id:
            yield {"kind": "check",
                   "name": "device_id matches expectation", "ok": False,
                   "detail": f"hello={device_id} "
                             f"expected={expected_device_id}"}
            yield {"kind": "fatal", "reason": "device_id mismatch"}
            return
        yield {"kind": "info", "text": f"device_id={device_id} "
                                       f"type={hello.device_type} "
                                       f"fw={hello.firmware_version}"}
        if wire_session_id:
            yield {"kind": "info",
                   "text": f"device-side session_id={wire_session_id}"}

        # 2. ADVERTISEMENT
        yield {"kind": "section",
               "title": "2. ADVERTISEMENT (server ∩ device)"}
        adv_doc = next((f for f in frames
                        if isinstance(f, dict)
                        and (f.get("type") == "ADVERTISEMENT"
                             or f.get("message_type")
                             == "capability_advertisement")), None)
        yield {"kind": "check", "name": "ADVERTISEMENT frame received",
               "ok": adv_doc is not None}
        adv = None
        if adv_doc:
            try:
                adv = XiaozhiCodec.parse_advertisement(adv_doc, device_id)
                yield {"kind": "check", "name": "ADVERTISEMENT valid",
                       "ok": adv.protocol_version == 1}
            except (ValueError, DeviceSessionError) as e:
                yield {"kind": "check", "name": "ADVERTISEMENT valid",
                       "ok": False, "detail": str(e)}
        advertised = adv.operations if adv else []
        yield {"kind": "info", "text": f"advertised: {advertised}"}
        set_led_ok = "SET_LED" in advertised
        yield {"kind": "check",
               "name": "SET_LED advertised BY THE DEVICE (not assumed)",
               "ok": set_led_ok}

        # sections 3-9 (shared implementation) — bidirectional
        # delegation so led_confirm answers flow back up
        sub = _validate_body(conn, timeout, hello, wire_session_id,
                             advertised, set_led_ok, expected_device_id)
        to_sub = None
        try:
            while True:
                event = sub.send(to_sub)
                to_sub = None
                if event["kind"] == "led_confirm":
                    to_sub = (yield event)
                else:
                    yield event
        except StopIteration:
            pass

    finally:
        if own_connection:
            conn.close()


#
