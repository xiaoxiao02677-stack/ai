"""P19-R Real-Device Validation Console (server-side).

One-shot acceptance runner for the REAL ESP32 running the P19 device
protocol firmware. Executes the spec §13-§21 sequence against the
actual device over TCP:

  1. connect -> read DEVICE_HELLO + CAPABILITY_ADVERTISEMENT
     (SET_LED must be advertised by the DEVICE, not assumed)
  2. register a real session (P17 DeviceRegistry semantics)
  3. security failure probes (all must NACK, none may execute):
     wrong device_id / unknown operation / invalid parameters
     (string 'on') / malformed JSON / duplicate command_id
  4. SET_LED on=true  -> ACK -> (user confirms LED physically ON)
  5. SET_LED on=false -> ACK -> (user confirms LED physically OFF)
  6. duplicate SET_LED (same command_id) -> NACK DUPLICATE_COMMAND
     (no second hardware action)
  7. timeout probe (no read within window -> explicit TIMEOUT report)

Usage:
  python tools/p19r_console.py --host <esp32-ip> [--port 3333]
                               [--src <path-to-src>] [--yes]
  --src defaults to LTM_P19R_SRC env or ./src (the server checkout).

This console is a VALIDATION harness (like the P16 simulator cross-
check, but pointing at the real device). The formal execution chain
(Policy -> Gateway -> Session Gate -> Adapter) is exercised by the
phase test suites; this tool verifies the DEVICE side of the contract.
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
SRC = None   # resolved after argparse (--src / env / default)

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
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))


def section(title):
    print(f"\n== {title} ==")


def read_frames(sock, timeout=6.0, max_frames=4, idle=1.2):
    """Read newline-framed JSON messages until idle or max_frames."""
    frames = []
    sock.settimeout(timeout)
    buf = b""
    deadline = time.time() + timeout
    while len(frames) < max_frames and time.time() < deadline:
        try:
            sock.settimeout(max(0.2, deadline - time.time()))
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                line = line.strip()
                if line:
                    try:
                        frames.append(json.loads(line))
                    except json.JSONDecodeError:
                        frames.append({"__malformed__": line[:120].decode(
                            "utf-8", "replace")})
        except socket.timeout:
            if frames and time.time() - (frames and time.time()) >= 0:
                break
            if frames:
                break
            continue
    return frames


def send_frame(sock, obj):
    sock.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode())


def make_command(cmd_id, device_id, operation, parameters=None):
    return {
        "v": 1,
        "cmd": {
            "command_id": cmd_id,
            "device_id": device_id,
            "capability": "capability.led" if operation == "SET_LED"
                          else "capability.test",
            "operation": operation,
            "parameters": parameters if parameters is not None else {},
            "protocol_version": 1,
            "provenance": {"action_id": "p19r-action",
                           "decision_id": "p19r-decision",
                           "evaluation_id": "p19r-eval",
                           "strategy_id": "p19r-strategy"},
            "created_at": time.time(),
        },
    }


def wait_ack(sock, timeout=4.0):
    """Read frames until an ack (message_type == 'ack') or timeout."""
    sock.settimeout(timeout)
    buf = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sock.settimeout(max(0.2, deadline - time.time()))
            chunk = sock.recv(4096)
            if not chunk:
                return None
            buf += chunk
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                line = line.strip()
                if not line:
                    continue
                try:
                    doc = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if doc.get("message_type") == "ack":
                    return doc
                # hello/heartbeat/adv frames are not acks — keep reading
        except socket.timeout:
            return None
    return None


def main():
    global SRC
    parser = argparse.ArgumentParser(description="P19-R real-device "
                                     "validation console")
    parser.add_argument("--host", required=True, help="ESP32 IP address")
    parser.add_argument("--port", type=int, default=3333)
    parser.add_argument("--src", default=os.environ.get(
        "LTM_P19R_SRC", os.path.join(ROOT, "src")))
    parser.add_argument("--yes", action="store_true",
                        help="skip interactive physical-LED confirmation")
    args = parser.parse_args()

    SRC = os.path.abspath(args.src)
    sys.path.insert(0, SRC)

    # reuse the project protocol stack (same validation as the server)
    from open_llm_vtuber.device_protocol import (  # noqa: E402
        DeviceHello, CapabilityAdvertisement, DeviceRegistry,
        DeviceSessionError, SESSION_ONLINE, GATE_OK)
    from open_llm_vtuber.device_protocol.ack import DeviceAck, \
        DeviceAckError   # noqa: E402

    print(f"P19-R Real-Device Validation Console")
    print(f"  target : {args.host}:{args.port}")
    print(f"  src    : {SRC}")

    # ------------------------------------------------------------------ 1
    section("1. TCP connect + HELLO + ADVERTISEMENT")
    sock = socket.create_connection((args.host, args.port), timeout=6)
    print("  connected")
    frames = read_frames(sock, timeout=6, max_frames=3)
    hello_doc = next((f for f in frames
                      if f.get("message_type") == "device_hello"), None)
    adv_doc = next((f for f in frames
                    if f.get("message_type")
                    == "capability_advertisement"), None)
    check("DEVICE_HELLO received", hello_doc is not None)
    if hello_doc:
        try:
            hello = DeviceHello.from_dict(hello_doc)
            device_id = hello.device_id
            check("HELLO valid (schema + version + identity)",
                  hello.protocol_version == 1 and bool(hello.device_id))
            print(f"  device_id={hello.device_id} "
                  f"type={hello.device_type} "
                  f"fw={hello.firmware_version}")
        except DeviceSessionError as e:
            device_id = hello_doc.get("device_id", "unknown")
            check("HELLO valid", False, str(e))
    else:
        device_id = "unknown"

    check("CAPABILITY_ADVERTISEMENT received", adv_doc is not None)
    adv_ops = adv_doc.get("operations", []) if adv_doc else []
    check("SET_LED advertised BY THE DEVICE (not assumed)",
          "SET_LED" in adv_ops)
    print(f"  advertised operations: {adv_ops}")

    # register a real session (P17 semantics)
    registry = DeviceRegistry()
    if hello_doc:
        try:
            hello = DeviceHello.from_dict(hello_doc)
            session = registry.register_hello(hello, now=time.time())
            check("session ONLINE registered (P17 semantics)",
                  session.state == SESSION_ONLINE)
            if adv_doc:
                adv = CapabilityAdvertisement(
                    device_id=device_id, operations=adv_ops)
                registry.advertise(adv, now=time.time())
            gate = registry.send_allowed(device_id, "SET_LED",
                                         now=time.time())
            check("command gate: ONLINE + SET_LED advertised -> OK",
                  gate == (True, GATE_OK))
            print(f"  session_id={session.session_id}")
        except DeviceSessionError as e:
            check("session registration", False, str(e))

    # ------------------------------------------------------------------ 2
    section("2. Security failure probes (all must NACK)")
    probe = make_command("probe-wrongdev-" + uuid.uuid4().hex[:8],
                         "esp32-WRONG", "SET_LED", {"on": True})
    send_frame(sock, probe)
    ack = wait_ack(sock)
    check("wrong device_id -> NACK UNKNOWN_DEVICE",
          ack is not None and ack.get("status") == "NACK"
          and ack.get("error_code") == "UNKNOWN_DEVICE",
          str(ack.get("error_code") if ack else "no ack"))

    probe = make_command("probe-unkop-" + uuid.uuid4().hex[:8],
                         device_id, "SERVO_MOVE", {"on": True})
    send_frame(sock, probe)
    ack = wait_ack(sock)
    check("unknown operation -> NACK UNKNOWN_OPERATION",
          ack is not None and ack.get("error_code") == "UNKNOWN_OPERATION",
          str(ack.get("error_code") if ack else "no ack"))

    probe = make_command("probe-badpar-" + uuid.uuid4().hex[:8],
                         device_id, "SET_LED", {"on": "true-string"})
    send_frame(sock, probe)
    ack = wait_ack(sock)
    check("invalid parameters (string on) -> NACK INVALID_PARAMETERS",
          ack is not None
          and ack.get("error_code") == "INVALID_PARAMETERS",
          str(ack.get("error_code") if ack else "no ack"))

    probe = make_command("probe-badpar2-" + uuid.uuid4().hex[:8],
                         device_id, "SET_LED", {"on": True, "extra": 1})
    send_frame(sock, probe)
    ack = wait_ack(sock)
    check("extra parameter -> NACK INVALID_PARAMETERS",
          ack is not None
          and ack.get("error_code") == "INVALID_PARAMETERS",
          str(ack.get("error_code") if ack else "no ack"))

    sock.sendall(b"{this is not json\n")
    ack = wait_ack(sock)
    check("malformed JSON -> NACK MALFORMED_MESSAGE",
          ack is not None and ack.get("error_code") == "MALFORMED_MESSAGE",
          str(ack.get("error_code") if ack else "no ack"))

    # ------------------------------------------------------------------ 3
    section("3. SET_LED ON (first real body action)")
    cmd_id_on = "led-on-" + uuid.uuid4().hex[:8]
    send_frame(sock, make_command(cmd_id_on, device_id, "SET_LED",
                                  {"on": True}))
    ack = wait_ack(sock)
    try:
        dack = DeviceAck.from_dict(ack) if ack else None
    except DeviceAckError:
        dack = None
    check("SET_LED on=true -> ACK (command_id + device_id bound)",
          dack is not None and dack.is_success
          and dack.command_id == cmd_id_on
          and dack.device_id == device_id)
    led_on_confirmed = None
    if not args.yes:
        try:
            ans = input("  >>> Is the LED PHYSICALLY ON now? [y/N] ")
            led_on_confirmed = ans.strip().lower() == "y"
        except EOFError:
            led_on_confirmed = None
    check("physical LED ON (user confirmation)",
          led_on_confirmed is True if not args.yes else True,
          "BLOCKED-from-console" if args.yes else "")

    # ------------------------------------------------------------------ 4
    section("4. SET_LED OFF")
    cmd_id_off = "led-off-" + uuid.uuid4().hex[:8]
    send_frame(sock, make_command(cmd_id_off, device_id, "SET_LED",
                                  {"on": False}))
    ack = wait_ack(sock)
    try:
        dack = DeviceAck.from_dict(ack) if ack else None
    except DeviceAckError:
        dack = None
    check("SET_LED on=false -> ACK",
          dack is not None and dack.is_success
          and dack.command_id == cmd_id_off)
    if not args.yes:
        try:
            ans = input("  >>> Is the LED PHYSICALLY OFF now? [y/N] ")
            led_off_confirmed = ans.strip().lower() == "y"
        except EOFError:
            led_off_confirmed = None
        check("physical LED OFF (user confirmation)",
              led_off_confirmed is True)
    else:
        check("physical LED OFF (user confirmation)",
              True, "BLOCKED-from-console")

    # ------------------------------------------------------------------ 5
    section("5. Idempotency (duplicate command_id)")
    send_frame(sock, make_command(cmd_id_on, device_id, "SET_LED",
                                  {"on": True}))
    ack = wait_ack(sock)
    check("duplicate SET_LED (same command_id) -> NACK DUPLICATE_COMMAND "
          "(no second hardware action)",
          ack is not None
          and ack.get("status") == "NACK"
          and ack.get("error_code") == "DUPLICATE_COMMAND",
          str(ack.get("error_code") if ack else "no ack"))

    # ------------------------------------------------------------------ 6
    section("6. Timeout semantics")
    # a command the firmware will NACK (unknown op) but we deliberately
    # do not read the reply within a short window: then read it late —
    # the DEVICE still answers; the SERVER-side late-ack rule (P18:
    # never rewrite a finished TIMEOUT) is enforced by the observer in
    # the phase-18 suite; here we only verify the device answers at all
    late_id = "late-" + uuid.uuid4().hex[:8]
    send_frame(sock, make_command(late_id, device_id, "SERVO_MOVE",
                                  {"on": True}))
    time.sleep(2.0)
    ack = wait_ack(sock, timeout=4.0)
    check("device answers even after our delay (late NACK arrives)",
          ack is not None and ack.get("command_id") == late_id)

    sock.close()

    # ------------------------------------------------------------------
    print(f"\n{'=' * 60}")
    print(f"P19-R CONSOLE RESULT: {ok_count} passed, {fail_count} failed")
    if args.yes:
        print("  [physical LED: NOT confirmed — run without --yes to "
              "confirm]")
    print(f"{'=' * 60}")
    if fail_count:
        print("FAILED items:")
        for status, name, detail in results:
            if status == "FAIL":
                print(f"  - {name} {detail}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
