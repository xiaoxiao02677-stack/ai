"""P19-R Stage-2 console test suite (Xiaozhi wire format).

Covers spec §16 (deterministic, no sleeps beyond explicit waits):
 1. HELLO parser (Xiaozhi flat + legacy envelope; rejects bad version/
    empty id/address-like id)
 2. ADVERTISEMENT parser (valid/missing SET_LED/schema rejects)
 3. Codec round trip: DeviceCommand -> wire -> ack -> DeviceAck
 4-7. Failure probes: wrong device / wrong version / unknown op /
    unsupported capability / invalid params (server-side rejections are
    classified, device-side NACKs asserted via a Xiaozhi simulator)
 8. ACK integrity (wrong command_id ignored; wrong device_id ignored;
    duplicate exactly-once)
 9. Duplicate command (firmware idempotency — no re-execution)
 10. Late ACK (P18 lifecycle stays TIMEOUT; late_ack flag)
 11. Kill switch (constant, no env setter; gateway routes via policy)
 12. Simulator E2E (full console run vs an in-process Xiaozhi firmware
     simulator: 27 checks green, executed [on, off, late-on], zero
     duplicate executions)

Run local:  sshagent/Scripts/python.exe tools/ltm_phase19r_tests.py
Run server: LTM_P19R_SRC=src uv run python tools/ltm_phase19r_tests.py
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_P19R_SRC", os.path.join(ROOT, "src"))

ok = 0
fail = 0
errors = []


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  OK   {name}")
    else:
        fail += 1
        errors.append(f"{name} {detail}")
        print(f"  FAIL {name} {detail}")


def section(t):
    print(f"\n== {t} ==")


sys.path.insert(0, os.path.abspath(SRC))

# import the SERVICE (codec's single home since the workshop
# extraction; the console is a thin CLI shell over it)
SERVICE = os.path.join(os.path.abspath(SRC), "open_llm_vtuber",
                       "device_protocol", "p19r_service.py")
CONSOLE = os.path.join(HERE, "p19r_console.py")
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("p19r_service", SERVICE)
svc = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(svc)
except ImportError:
    # server-style src. package layout: import normally
    import importlib
    svc = importlib.import_module(
        "open_llm_vtuber.device_protocol.p19r_service")
console = svc   # codec lives here now

from open_llm_vtuber.device_protocol import (  # noqa: E402
    DeviceCommand, DeviceHello, DeviceSessionError, DeviceAck,
    CapabilityAdvertisement, DeviceRegistry, DeviceObserver,
    SESSION_ONLINE, GATE_OK)
from open_llm_vtuber.device_protocol.observer import (
    LIFECYCLE_ACKED, LIFECYCLE_TIMEOUT)  # noqa: E402

DEV = "xiaozhi-esp32s3-001"


def mk_cmd(cid, op="SET_LED", params=None, dev=DEV, capability=None):
    c = DeviceCommand(command_id=cid, device_id=dev,
                      capability=capability or ("capability.led"
                                                if op == "SET_LED"
                                                else "capability.test"),
                      operation=op, parameters=params or {"on": True},
                      protocol_version=1,
                      provenance={"action_id": "t", "decision_id": "t",
                                  "evaluation_id": "t", "strategy_id": "t"},
                      created_at=1.0)
    c.validate()
    return c


# ---------------------------------------------------------------------------
section("1. HELLO parser (Xiaozhi flat + legacy)")
hello, wsess = console.XiaozhiCodec.parse_hello(
    {"type": "HELLO", "protocol_version": 1, "device_id": DEV,
     "firmware": "xiaozhi-1.0", "session_id": "wire-1"})
check("flat HELLO parses -> DeviceHello (firmware mapped)",
      hello.device_id == DEV and hello.firmware_version == "xiaozhi-1.0"
      and wsess == "wire-1")
hello2, wsess2 = console.XiaozhiCodec.parse_hello(
    {"message_type": "device_hello", "protocol_version": 1,
     "device_id": DEV, "device_type": "t", "firmware_version": "v9"})
check("legacy envelope HELLO also parses",
      hello2.device_id == DEV and wsess2 is None)
for bad, expect in (
        ({"type": "HELLO", "protocol_version": 2, "device_id": DEV,
          "firmware": "f"}, "unsupported"),
        ({"type": "HELLO", "protocol_version": 1, "device_id": "",
          "firmware": "f"}, "device_id"),
        ({"type": "HELLO", "protocol_version": 1,
          "device_id": "192.168.2.7", "firmware": "f"}, "transport"),
        ({"type": "NOT_HELLO", "protocol_version": 1, "device_id": DEV},
         "hello frame")):
    try:
        console.XiaozhiCodec.parse_hello(bad)
        check(f"bad HELLO rejected ({expect})", False)
    except DeviceSessionError as e:
        check(f"bad HELLO rejected ({expect})", expect in str(e))

# ---------------------------------------------------------------------------
section("2. ADVERTISEMENT parser")
adv = console.XiaozhiCodec.parse_advertisement(
    {"type": "ADVERTISEMENT", "protocol_version": 1, "device_id": DEV,
     "operations": ["SET_LED"]}, DEV)
check("flat ADVERTISEMENT parses -> CapabilityAdvertisement",
      adv.operations == ["SET_LED"] and adv.device_id == DEV)
check("missing SET_LED in advertisement is visible (intersection rule)",
      console.XiaozhiCodec.parse_advertisement(
          {"type": "ADVERTISEMENT", "protocol_version": 1,
           "device_id": DEV, "operations": []}, DEV).operations == [])
try:
    console.XiaozhiCodec.parse_advertisement(
        {"type": "ADVERTISEMENT", "protocol_version": 1, "device_id": DEV,
         "operations": ["SET_LED"], "evil": 1}, DEV)
    check("unknown advertisement field rejected (closed schema)", False)
except Exception:
    check("unknown advertisement field rejected (closed schema)", True)

# ---------------------------------------------------------------------------
section("3. Codec round trip")
cmd = mk_cmd("c-rt-1")
wire = console.XiaozhiCodec.encode_command(cmd)
check("encode: flat wire frame shape",
      wire["type"] == "COMMAND" and wire["command_id"] == "c-rt-1"
      and wire["operation"] == "SET_LED"
      and wire["parameters"] == {"on": True}
      and wire["protocol_version"] == 1)
ack = console.XiaozhiCodec.parse_ack(
    {"type": "ACK", "protocol_version": 1, "command_id": "c-rt-1",
     "device_id": DEV, "error_code": None, "echo": {"on": True},
     "created_at": 1.0})
check("flat ACK parses -> DeviceAck (closed schema validated)",
      ack is not None and ack.is_success
      and ack.command_id == "c-rt-1")
nack = console.XiaozhiCodec.parse_ack(
    {"type": "NACK", "protocol_version": 1, "command_id": "c-rt-2",
     "device_id": DEV, "error_code": "UNKNOWN_OPERATION",
     "created_at": 1.0})
check("flat NACK parses with error_code",
      nack is not None and not nack.is_success
      and nack.error_code == "UNKNOWN_OPERATION")
check("non-ack frames return None (hello not misread)",
      console.XiaozhiCodec.parse_ack(
          {"type": "HELLO", "protocol_version": 1, "device_id": DEV})
      is None)

# ---------------------------------------------------------------------------
section("4-7. Failure probes (server-side classification)")
# the console probe() classifies: server-side schema rejection (device
# never sees it) is a PASS for commands P15 rejects up front; the device
# NACK path is covered in section 12 E2E
try:
    mk_cmd("x", op="SERVO_MOVE", capability="capability.test")
    check("unknown operation rejected at command construction", False)
except Exception:
    check("unknown operation rejected at command construction", True)
try:
    mk_cmd("x", params={"on": "true-string"})
    check("string 'on' passes command layer (closed typing) but device "
          "must NACK", True)
except Exception:
    check("string 'on' passes command layer (closed typing) but device "
          "must NACK", False)

# ---------------------------------------------------------------------------
section("8. ACK integrity (P18 observer, unit)")
reg = DeviceRegistry()
obs = DeviceObserver(reg)
reg.register_hello(DeviceHello(device_id=DEV, device_type="t",
                               firmware_version="f"), now=time.time())
obs.observe_command_created("k1", DEV, "SET_LED", now=time.time())
obs.observe_command_sent("k1", now=time.time())
check("wrong command_id ACK ignored",
      not obs.observe_ack(DeviceAck(command_id="other", device_id=DEV,
                                    status="ACK")))
check("wrong device_id ACK ignored",
      not obs.observe_ack(DeviceAck(command_id="k1", device_id="esp32-X",
                                    status="ACK")))
check("valid ACK completes (exactly once)",
      obs.observe_ack(DeviceAck(command_id="k1", device_id=DEV,
                                status="ACK"), now=time.time())
      and not obs.observe_ack(DeviceAck(command_id="k1", device_id=DEV,
                                        status="ACK")))
check("lifecycle ACKED",
      obs.get_command_status("k1").status == LIFECYCLE_ACKED)

# ---------------------------------------------------------------------------
section("9-10. Duplicate + late ACK (P18 unit)")
obs.observe_command_created("k2", DEV, "SET_LED", now=time.time())
obs.observe_command_sent("k2", now=time.time())
obs.observe_terminal("k2", LIFECYCLE_TIMEOUT, error_code="TIMEOUT",
                     now=time.time())
check("late ACK does not rewrite TIMEOUT",
      not obs.observe_ack(DeviceAck(command_id="k2", device_id=DEV,
                                    status="ACK"), now=time.time())
      and obs.get_command_status("k2").status == LIFECYCLE_TIMEOUT)
check("late_ack flag set (observability only)",
      obs.get_command_status("k2").late_ack is True)

# ---------------------------------------------------------------------------
section("11. Kill switch (code-level)")
import re  # noqa: E402
policy_src = open(os.path.join(os.path.abspath(SRC), "open_llm_vtuber",
                               "execution", "policy.py"),
                  encoding="utf-8").read()
policy_code = re.sub(r'""".*?"""', "", policy_src, flags=re.DOTALL)
policy_code = re.sub(r"#[^\n]*", "", policy_code)
check("GLOBAL_EXECUTION_ENABLED = False",
      "GLOBAL_EXECUTION_ENABLED = False" in policy_code)
check("no env/config override for the kill switch",
      policy_code.count("GLOBAL_EXECUTION_ENABLED") == 2
      and "os.environ" not in policy_code and "def set_" not in policy_code)
gateway_src = open(os.path.join(os.path.abspath(SRC), "open_llm_vtuber",
                                "execution", "gateway.py"),
                   encoding="utf-8").read()
check("gateway routes through policy.decide (no second path)",
      "self.policy.decide(" in gateway_src)

# ---------------------------------------------------------------------------
section("12. Simulator E2E (full console run vs Xiaozhi firmware sim)")


class XZSim(threading.Thread):
    """Xiaozhi-style firmware simulator: flat wire format, full
    validation pipeline (version/device/op/params/idempotency)."""

    def __init__(self, advertise_set_led=True):
        super().__init__(daemon=True)
        self.advertise_set_led = advertise_set_led
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        self.srv.listen(2)
        self.executed = []
        self.seen = set()

    def run(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                break
            threading.Thread(target=self.h, args=(c,), daemon=True).start()

    def h(self, c):
        try:
            c.sendall((json.dumps(
                {"type": "HELLO", "protocol_version": 1,
                 "device_id": DEV, "firmware": "xiaozhi-1.0",
                 "session_id": "wire-1"}) + "\n").encode())
            ops = ["SET_LED"] if self.advertise_set_led else []
            c.sendall((json.dumps(
                {"type": "ADVERTISEMENT", "protocol_version": 1,
                 "device_id": DEV, "operations": ops}) + "\n").encode())
            buf = b""
            c.settimeout(30)
            while True:
                try:
                    d = c.recv(4096)
                except socket.timeout:
                    break
                if not d:
                    break
                buf += d
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    if not line.strip():
                        continue
                    try:
                        doc = json.loads(line)
                    except json.JSONDecodeError:
                        self.nack(c, "", "MALFORMED_MESSAGE")
                        continue
                    if doc.get("type") == "HEARTBEAT_ACK":
                        continue
                    if doc.get("type") != "COMMAND":
                        self.nack(c, doc.get("command_id", ""),
                                  "INVALID_SCHEMA")
                        continue
                    if doc.get("protocol_version") != 1:
                        self.nack(c, doc.get("command_id", ""),
                                  "UNSUPPORTED_VERSION")
                        continue
                    cid = doc.get("command_id")
                    dev = doc.get("device_id")
                    op = doc.get("operation")
                    if dev != DEV:
                        self.nack(c, cid or "", "UNKNOWN_DEVICE")
                        continue
                    if op not in ("TEST_ECHO", "SET_LED"):
                        self.nack(c, cid or "", "UNKNOWN_OPERATION")
                        continue
                    p = doc.get("parameters", {})
                    if op == "SET_LED" and (
                            set(p.keys()) != {"on"}
                            or not isinstance(p.get("on"), bool)):
                        self.nack(c, cid, "INVALID_PARAMETERS")
                        continue
                    if cid in self.seen:
                        self.nack(c, cid, "DUPLICATE_COMMAND")
                        continue
                    self.seen.add(cid)
                    if op == "SET_LED":
                        self.executed.append(p["on"])
                    self.ack(c, cid, p)
        except OSError:
            pass
        finally:
            c.close()

    def ack(self, c, cid, echo):
        c.sendall((json.dumps(
            {"type": "ACK", "protocol_version": 1, "command_id": cid,
             "device_id": DEV, "error_code": None, "echo": echo,
             "created_at": time.time()}) + "\n").encode())

    def nack(self, c, cid, err):
        c.sendall((json.dumps(
            {"type": "NACK", "protocol_version": 1, "command_id": cid,
             "device_id": DEV, "error_code": err, "echo": {},
             "created_at": time.time()}) + "\n").encode())


sim = XZSim()
sim.start()
time.sleep(0.2)
r = subprocess.run(
    [sys.executable, CONSOLE, "--host", "127.0.0.1",
     "--port", str(sim.port), "--yes", "--src", os.path.abspath(SRC)],
    capture_output=True, text=True, timeout=180)
out = r.stdout
check("console E2E exits 0", r.returncode == 0,
      out[-300:] if r.returncode else "")
check("console reports 26 passed / 0 failed (thin shell)",
      "26 passed, 0 failed" in out)
check("device executed [on, off, late-on] — zero duplicates",
      sim.executed == [True, False, True], str(sim.executed))
check("session gate + P17 semantics exercised in E2E",
      "session ONLINE (P17)" in out
      and "Device Gate: ONLINE + SET_LED -> OK" in out)
check("physical LED marked not-confirmed under --yes",
      "NOT confirmed" in out)
check("kill switch checks present in E2E",
      "GLOBAL_EXECUTION_ENABLED = False" in out)

# negative E2E: device NOT advertising SET_LED must stop before LED tests
sim2 = XZSim(advertise_set_led=False)
sim2.start()
time.sleep(0.2)
r2 = subprocess.run(
    [sys.executable, CONSOLE, "--host", "127.0.0.1",
     "--port", str(sim2.port), "--yes", "--src", os.path.abspath(SRC)],
    capture_output=True, text=True, timeout=120)
check("no SET_LED advertisement -> console refuses LED tests "
      "(intersection rule enforced)",
      r2.returncode == 1
      and "did not advertise SET_LED" in r2.stdout
      and "SET_LED ON" not in r2.stdout)
check("refused run executed zero LED actions",
      sim2.executed == [])

print(f"\n{'=' * 58}")
print(f"P19-R STAGE-2 TESTS: {ok} passed, {fail} failed")
print(f"{'=' * 58}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
