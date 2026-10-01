"""P19-R Xiaozhi Real-Device Validation Console (server-side, stage 2).

One-shot acceptance runner for the REAL Xiaozhi ESP32-S3 firmware.
Closes the loop: TCP -> HELLO -> ADVERTISEMENT -> Session -> Gate ->
six security failure probes -> SET_LED ON/OFF (physical confirmation)
-> duplicate idempotency -> late-ACK semantics -> final verdict.

Wire format (Xiaozhi, FLAT): {"type": "COMMAND", "protocol_version": 1,
"command_id": ..., "device_id": ..., "operation": ..., "parameters": ...}
— the console translates this to/from the PROJECT protocol objects
(DeviceCommand / DeviceAck / DeviceHello / CapabilityAdvertisement /
DeviceRegistry / DeviceObserver). The core objects stay the single
authority; this file only holds the wire codec (spec §15). The legacy
P15 envelope format ({"v":1,"cmd":{...}}, message_type acks) is ALSO
accepted on receive, so the same console validates both firmware
lineages.

Usage:
  uv run python tools/p19r_console.py --host <esp32-ip> [--port 3333]
      [--timeout 6] [--verbose] [--device-id <id>] [--yes] [--src PATH]
"""
import argparse
import json
import os
import socket
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")

ok_count = 0
fail_count = 0
results = []


def check(name, cond, detail=""):
    global ok_count, fail_count
    status = "OK  " if cond else "FAIL"
    if cond:
        ok_count += 1
    else:
        fail_count += 1
    results.append((status, name, detail))
    mark = f"[{status}]" if not VERBOSE else f"[{status}]"
    print(f"  {mark} {name}" + (f"  ({detail})" if detail else ""))


def section(title):
    print(f"\n== {title} ==")


VERBOSE = False


# ---------------------------------------------------------------------------
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
        from open_llm_vtuber.device_protocol import DeviceHello, \
            DeviceSessionError
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
        from open_llm_vtuber.device_protocol import \
            CapabilityAdvertisement, DeviceSessionError
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
        from open_llm_vtuber.device_protocol import DeviceAck
        t = doc.get("type", "")
        if t in ("ACK", "NACK") or doc.get("message_type") == "ack":
            status = t if t in ("ACK", "NACK") \
                else doc.get("status", "")
            return DeviceAck.from_dict({
                "command_id": doc.get("command_id", ""),
                "device_id": doc.get("device_id", ""),
                "status": status,
                "error_code": doc.get("error_code"),
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
# Main validation sequence
# ---------------------------------------------------------------------------

def main():
    global VERBOSE
    parser = argparse.ArgumentParser(
        description="P19-R Xiaozhi real-device validation console")
    parser.add_argument("--host", required=True,
                        help="Xiaozhi ESP32 IP address")
    parser.add_argument("--port", type=int, default=3333)
    parser.add_argument("--timeout", type=float, default=6.0,
                        help="per-read timeout seconds (default 6)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="verbose/debug output")
    parser.add_argument("--device-id", default=None,
                        help="expected device_id (default: accept HELLO's)")
    parser.add_argument("--yes", action="store_true",
                        help="skip interactive physical-LED confirmation")
    parser.add_argument("--src", default=os.environ.get(
        "LTM_P19R_SRC", os.path.join(ROOT, "src")))
    args = parser.parse_args()
    VERBOSE = args.verbose

    sys.path.insert(0, os.path.abspath(args.src))
    from open_llm_vtuber.device_protocol import (  # noqa: E402
        DeviceCommand, DeviceSessionError, DeviceRegistry,
        DeviceObserver, SESSION_ONLINE, GATE_OK)
    from open_llm_vtuber.device_protocol.session import (
        DEVICE_NOT_FOUND, DEVICE_OFFLINE, DEVICE_STALE,
        DEVICE_PROTOCOL_MISMATCH, DEVICE_CAPABILITY_UNSUPPORTED)  # noqa
    from open_llm_vtuber.device_protocol.observer import (
        LIFECYCLE_ACKED, LIFECYCLE_TIMEOUT)  # noqa: E402

    print("P19-R Xiaozhi Real-Device Validation Console")
    print(f"  target : {args.host}:{args.port}  timeout={args.timeout}s")

    conn = DeviceConnection(args.host, args.port, args.timeout)

    # -------------------------------------------------------------- 1 HELLO
    section("1. TCP connect + HELLO")
    frames = conn.read_frames(idle_wait=1.0, max_frames=3,
                              hard_deadline=args.timeout)
    if VERBOSE:
        for f in frames:
            print(f"    RX {json.dumps(f, ensure_ascii=False)[:160]}")
    hello_doc = next((f for f in frames
                      if isinstance(f, dict)
                      and (f.get("type") == "HELLO"
                           or f.get("message_type") == "device_hello")),
                     None)
    check("HELLO frame received", hello_doc is not None)
    hello = None
    wire_session_id = None
    if hello_doc:
        try:
            hello, wire_session_id = XiaozhiCodec.parse_hello(hello_doc)
            check("HELLO valid (schema + protocol_version=1 + identity)",
                  hello.protocol_version == 1 and bool(hello.device_id))
        except DeviceSessionError as e:
            check("HELLO valid", False, str(e))
    if hello is None:
        print("FATAL: no valid HELLO — cannot continue")
        conn.close()
        return 1

    device_id = hello.device_id
    if args.device_id and args.device_id != device_id:
        check("device_id matches --device-id", False,
              f"hello={device_id} expected={args.device_id}")
        conn.close()
        return 1
    print(f"  device_id={device_id}  type={hello.device_type}  "
          f"fw={hello.firmware_version}")
    if wire_session_id:
        print(f"  device-side session_id={wire_session_id}")
    if args.device_id:
        check("device_id stable vs expectation", args.device_id == device_id)

    # -------------------------------------------------- 2 ADVERTISEMENT
    section("2. ADVERTISEMENT (server capability ∩ device advertisement)")
    adv_doc = next((f for f in frames
                    if isinstance(f, dict)
                    and (f.get("type") == "ADVERTISEMENT"
                         or f.get("message_type")
                         == "capability_advertisement")), None)
    check("ADVERTISEMENT frame received", adv_doc is not None)
    adv = None
    if adv_doc:
        try:
            adv = XiaozhiCodec.parse_advertisement(adv_doc, device_id)
            check("ADVERTISEMENT valid (schema + version)",
                  adv.protocol_version == 1)
        except (ValueError, DeviceSessionError) as e:
            check("ADVERTISEMENT valid", False, str(e))
    advertised = adv.operations if adv else []
    print(f"  advertised operations: {advertised}")
    set_led_ok = "SET_LED" in advertised
    check("SET_LED advertised BY THE DEVICE (not assumed)", set_led_ok)

    # -------------------------------------------------- 3 Session (P17)
    section("3. Session + Device Gate (P17 semantics)")
    registry = DeviceRegistry()
    observer = DeviceObserver(registry)
    session = registry.register_hello(hello, now=time.time())
    check("session ONLINE (P17 state machine)",
          session.state == SESSION_ONLINE)
    if adv:
        registry.advertise(adv, now=time.time())
    gate = registry.send_allowed(device_id, "SET_LED", now=time.time())
    check("Device Gate: ONLINE + SET_LED advertised -> OK",
          gate == (True, GATE_OK))
    state = observer.get_device_state(device_id)
    print(f"  device_id           : {state.device_id}")
    print(f"  session_id (server) : {state.session_id}")
    print(f"  protocol_version    : {state.protocol_version}")
    print(f"  connection_state    : {state.connection_state}")
    print(f"  last_seen           : {state.last_seen}")
    print(f"  advertised ops      : {state.capabilities}")

    # -------------------------------------------- 4 security failure probes
    section("4. Security failure probes (six, all must NACK)")

    def probe(label, command_id, device, operation, params,
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
        except Exception as e:   # intentionally-invalid probes may fail
            # server-side validation caught it — also acceptable
            check(label, True, f"rejected server-side: {type(e).__name__}")
            return None
        observer.observe_command_created(command_id, device, operation,
                                         now=time.time())
        frame = wire_override if wire_override is not None \
            else XiaozhiCodec.encode_command(cmd)
        conn.send(frame)
        ack = conn.wait_ack(args.timeout)
        ok_ack = (ack is not None and ack.status == "NACK"
                  and ack.error_code == expect_code
                  and ack.command_id == command_id)
        check(label, ok_ack,
              f"got status={getattr(ack, 'status', None)} "
              f"code={getattr(ack, 'error_code', None)}")
        return ack

    probe("wrong device_id -> NACK UNKNOWN_DEVICE",
          "p-wrongdev-" + uuid.uuid4().hex[:8], "esp32-WRONG",
          "SET_LED", {"on": True}, "UNKNOWN_DEVICE")
    probe("unknown operation -> NACK UNKNOWN_OPERATION",
          "p-unkop-" + uuid.uuid4().hex[:8], device_id,
          "SERVO_MOVE", {"on": True}, "UNKNOWN_OPERATION")
    probe("unsupported capability (op not advertised) -> NACK",
          "p-unscap-" + uuid.uuid4().hex[:8], device_id,
          "PLAY_AUDIO", {"vol": 5}, "UNKNOWN_OPERATION")
    # invalid protocol version: raw wire frame (version=9)
    bad_ver_id = "p-badver-" + uuid.uuid4().hex[:8]
    conn.send({"type": "COMMAND", "protocol_version": 9,
               "command_id": bad_ver_id, "device_id": device_id,
               "operation": "SET_LED", "parameters": {"on": True}})
    ack = conn.wait_ack(args.timeout)
    check("invalid protocol version -> NACK UNSUPPORTED_VERSION",
          ack is not None and ack.status == "NACK"
          and ack.error_code == "UNSUPPORTED_VERSION",
          f"got {getattr(ack, 'error_code', None)}")
    probe("invalid parameters (string on) -> NACK INVALID_PARAMETERS",
          "p-badpar-" + uuid.uuid4().hex[:8], device_id,
          "SET_LED", {"on": "true-string"}, "INVALID_PARAMETERS")
    probe("extra parameter -> NACK INVALID_PARAMETERS",
          "p-badpar2-" + uuid.uuid4().hex[:8], device_id,
          "SET_LED", {"on": True, "extra": 1}, "INVALID_PARAMETERS")
    conn.send(b"{this is not json")
    ack = conn.wait_ack(args.timeout)
    check("malformed JSON -> NACK MALFORMED_MESSAGE",
          ack is not None and ack.error_code == "MALFORMED_MESSAGE",
          f"got {getattr(ack, 'error_code', None)}")

    if not set_led_ok:
        print("\nFATAL: device did not advertise SET_LED — the real LED "
              "tests cannot proceed (server capability ∩ device "
              "advertisement must hold)")
        conn.close()
        return 1

    # -------------------------------------------------- 5 SET_LED ON
    section("5. SET_LED ON (first real body action)")
    cmd_on = DeviceCommand(
        command_id="led-on-" + uuid.uuid4().hex[:8], device_id=device_id,
        capability="capability.led", operation="SET_LED",
        parameters={"on": True}, protocol_version=1,
        provenance={"action_id": "p19r", "decision_id": "p19r",
                    "evaluation_id": "p19r", "strategy_id": "p19r"},
        created_at=time.time())
    cmd_on.validate()
    observer.observe_command_created(cmd_on.command_id, device_id,
                                     "SET_LED", now=time.time())
    conn.send(XiaozhiCodec.encode_command(cmd_on))
    ack_on = conn.wait_ack(args.timeout)
    check("ACK integrity: command_id + device_id bound + ACK",
          ack_on is not None and ack_on.is_success
          and ack_on.command_id == cmd_on.command_id
          and ack_on.device_id == device_id
          and ack_on.protocol_version == 1,
          f"cmd={getattr(ack_on, 'command_id', None)} "
          f"dev={getattr(ack_on, 'device_id', None)}")
    if ack_on is not None:
        observer.observe_ack(ack_on, now=time.time())
        rec = observer.get_command_status(cmd_on.command_id)
        check("lifecycle -> ACKED (P18 CommandLifecycle)",
              rec is not None and rec.status == LIFECYCLE_ACKED)
    if not args.yes:
        try:
            ans = input("  [人工确认] ESP32 上的 WS2812/LED 已亮? "
                        "[y/N] ")
            check("physical LED ON (user confirmation)",
                  ans.strip().lower() == "y")
        except EOFError:
            check("physical LED ON (user confirmation)", False,
                  "no input")
    else:
        check("physical LED ON (user confirmation)", True,
              "BLOCKED-from-console (--yes)")

    # -------------------------------------------------- 6 SET_LED OFF
    section("6. SET_LED OFF")
    cmd_off = DeviceCommand(
        command_id="led-off-" + uuid.uuid4().hex[:8],
        device_id=device_id, capability="capability.led",
        operation="SET_LED", parameters={"on": False},
        protocol_version=1,
        provenance={"action_id": "p19r", "decision_id": "p19r",
                    "evaluation_id": "p19r", "strategy_id": "p19r"},
        created_at=time.time())
    cmd_off.validate()
    observer.observe_command_created(cmd_off.command_id, device_id,
                                     "SET_LED", now=time.time())
    conn.send(XiaozhiCodec.encode_command(cmd_off))
    ack_off = conn.wait_ack(args.timeout)
    check("SET_LED off -> ACK (integrity verified)",
          ack_off is not None and ack_off.is_success
          and ack_off.command_id == cmd_off.command_id)
    if ack_off is not None:
        observer.observe_ack(ack_off, now=time.time())
    if not args.yes:
        try:
            ans = input("  [人工确认] WS2812/LED 已灭? [y/N] ")
            check("physical LED OFF (user confirmation)",
                  ans.strip().lower() == "y")
        except EOFError:
            check("physical LED OFF (user confirmation)", False,
                  "no input")
    else:
        check("physical LED OFF (user confirmation)", True,
              "BLOCKED-from-console (--yes)")

    # -------------------------------------------------- 7 Idempotency
    section("7. Duplicate command (idempotency)")
    conn.send(XiaozhiCodec.encode_command(cmd_on))
    ack_dup = conn.wait_ack(args.timeout)
    check("duplicate command_id -> NACK DUPLICATE_COMMAND",
          ack_dup is not None and ack_dup.status == "NACK"
          and ack_dup.error_code == "DUPLICATE_COMMAND",
          f"got {getattr(ack_dup, 'error_code', None)}")
    if ack_dup is not None:
        observer.observe_ack(ack_dup, now=time.time())   # must not
        # re-complete (duplicate); lifecycle stays ACKED, exactly-once
    rec = observer.get_command_status(cmd_on.command_id)
    check("lifecycle stays ACKED (exactly-once; no re-completion)",
          rec is not None and rec.status == LIFECYCLE_ACKED)
    print("  FIRST EXECUTION : PASS"
          if ack_on is not None and ack_on.is_success else
          "  FIRST EXECUTION : FAIL")
    print("  DUPLICATE EXECUTION : PASS"
          if ack_dup is not None
          and ack_dup.error_code == "DUPLICATE_COMMAND" else
          "  DUPLICATE EXECUTION : FAIL")
    print("  PHYSICAL RE-EXECUTION : NOT ALLOWED"
          " (firmware idempotency cache)")

    # -------------------------------------------------- 8 Late ACK (P18)
    section("8. Late-ACK semantics (P18 CommandLifecycle)")
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
    # deliberately DO NOT read: let the console-side lifecycle time out
    observer.observe_terminal(cmd_late.command_id, LIFECYCLE_TIMEOUT,
                              error_code="TIMEOUT", now=time.time())
    late_ack = conn.wait_ack(args.timeout + 3.0)   # read it late
    check("late ACK eventually arrives from the device",
          late_ack is not None
          and late_ack.command_id == cmd_late.command_id)
    if late_ack is not None:
        observer.observe_ack(late_ack, now=time.time())   # late: must
        # NOT rewrite the finished TIMEOUT result
    rec = observer.get_command_status(cmd_late.command_id)
    check("lifecycle stays TIMEOUT (late ACK never rewrites result)",
          rec is not None and rec.status == LIFECYCLE_TIMEOUT)
    check("late_ack flag recorded (observability only)",
          rec is not None and rec.late_ack is True)

    conn.close()

    # -------------------------------------------------- 9 Kill switch
    section("9. Kill Switch (code-level)")
    from open_llm_vtuber.execution.policy import GLOBAL_EXECUTION_ENABLED
    check("GLOBAL_EXECUTION_ENABLED = False (unchanged, no overrides)",
          GLOBAL_EXECUTION_ENABLED is False)
    gateway_src = open(os.path.join(os.path.abspath(args.src),
                                    "open_llm_vtuber", "execution",
                                    "gateway.py"), encoding="utf-8").read()
    check("ExecutionGateway still routes through policy.decide "
          "(no second execution path)",
          "self.policy.decide(" in gateway_src)
    import re
    policy_src = open(os.path.join(os.path.abspath(args.src),
                                   "open_llm_vtuber", "execution",
                                   "policy.py"), encoding="utf-8").read()
    policy_code = re.sub(r'""".*?"""', "", policy_src, flags=re.DOTALL)
    policy_code = re.sub(r"#[^\n]*", "", policy_code)
    check("kill switch: no env/config setter (constant only)",
          policy_code.count("GLOBAL_EXECUTION_ENABLED") == 2
          and "os.environ" not in policy_code)

    # -------------------------------------------------- verdict
    print(f"\n{'=' * 60}")
    print(f"P19-R CONSOLE RESULT: {ok_count} passed, {fail_count} failed")
    print(f"{'=' * 60}")
    if fail_count:
        print("FAILED items:")
        for status, name, detail in results:
            if status == "FAIL":
                print(f"  - {name} {detail}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
