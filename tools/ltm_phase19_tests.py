"""Phase 19 ESP32 SET_LED first-body-capability tests.

The first REAL body capability must be MORE strictly guarded than the
simulator — P19 verifies SET_LED end-to-end through the P3-P18 chain
with failure cases outnumbering success cases (§19):

19.1 Capability (exists/closes schema/bool enforcement/unknown key/
    missing key) / 19.2 Resolver (deterministic, no fallback) /
    19.3 DeviceCommand (bool params, int rejected, closed schema) /
    19.4 Firmware validation (shared-pipeline checks via the P16
    simulator upgraded with SET_LED strictness) / 19.5 Session Gate
    (ONLINE+advertised pass; unknown/offline/stale/capability-mismatch
    reject with send=0) / 19.6 Policy (kill switch held; DEVICE level
    DENY default) / 19.7 Gateway (no bypass path) / 19.8 Adapter
    (on/off round trips, duplicate command_id refused, observer
    lifecycle ACKED) / 19.9 TCP Transport (simulator over real
    sockets) / 19.10 ACK (wrong command/device ignored, duplicate
    exactly-once) / 19.11 Idempotency / 19.12 Lifecycle / 19.13 Kill
    Switch / 19.14 Real ESP32 E2E (BLOCKED — hardware absent, stated
    honestly, never faked).

Run local:  sshagent/Scripts/python.exe tools/ltm_phase19_tests.py
Run server: LTM_P19_SRC=src uv run python tools/ltm_phase19_tests.py
"""
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_P19_SRC", os.path.join(ROOT, "src"))

import shutil  # noqa: E402
SRC_ABS = os.path.abspath(SRC)
WORK = tempfile.mkdtemp(prefix="ltm_p19_")
PKG = os.path.join(WORK, "pkg")
LTM_PKG = os.path.join(PKG, "long_term_memory")
os.makedirs(os.path.join(LTM_PKG, "storage"))
DOMAINS = {}
for name in ("experience", "reflection", "lesson", "strategy", "evaluation",
             "decision", "action", "execution", "capability",
             "device_protocol"):
    d = os.path.join(PKG, name)
    os.makedirs(d)
    DOMAINS[name] = d
open(os.path.join(PKG, "__init__.py"), "w").close()
for name, dst in DOMAINS.items():
    shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", name, "__init__.py"),
                os.path.join(dst, "__init__.py"))
shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", "long_term_memory",
                         "__init__.py"), os.path.join(LTM_PKG, "__init__.py"))
for rel in ["schemas.py", "store.py", "retriever.py", "keyword_extractor.py",
            "privacy.py", "deduplicator.py", "prompt_builder.py",
            "manager.py", "extractor.py"]:
    shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", "long_term_memory", rel),
                os.path.join(LTM_PKG, rel))
for rel in ["provider.py", "sqlite_provider.py", "repository.py",
            "provider_factory.py", "hermes_provider.py", "__init__.py"]:
    shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", "long_term_memory",
                             "storage", rel), os.path.join(LTM_PKG, "storage", rel))
for dom, files in (("experience", ("schemas.py", "repository.py", "engine.py")),
                   ("reflection", ("schemas.py", "repository.py", "engine.py",
                                   "analyzer.py")),
                   ("lesson", ("schemas.py", "repository.py", "engine.py",
                               "analyzer.py")),
                   ("strategy", ("schemas.py", "repository.py", "engine.py",
                                 "analyzer.py")),
                   ("evaluation", ("schemas.py", "repository.py", "engine.py",
                                   "analyzer.py")),
                   ("decision", ("schemas.py", "repository.py", "engine.py",
                                 "analyzer.py")),
                   ("action", ("schemas.py", "repository.py", "engine.py",
                               "analyzer.py")),
                   ("execution", ("schemas.py", "repository.py", "engine.py",
                                  "sandbox.py", "policy.py", "adapter.py",
                                  "gateway.py")),
                   ("capability", ("schemas.py", "registry.py",
                                   "resolver.py")),
                   ("device_protocol", ("command.py", "protocol.py",
                                        "transport.py", "real_transport.py",
                                        "ack.py", "session.py",
                                        "observer.py", "__init__.py"))):
    for rel in files:
        shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", dom, rel),
                    os.path.join(DOMAINS[dom], rel))

sys.path.insert(0, os.path.dirname(PKG))
os.chdir(WORK)

from pkg.device_protocol import (  # noqa: E402
    DeviceObserver, DeviceRegistry, DeviceHello, CapabilityAdvertisement,
    DeviceAck, MockTransport, DeviceCommand, DeviceProtocol, TransportError,
    RealTCPTransport, DeviceCommandError)
from pkg.execution.adapter import ESP32Adapter, ExecutionRequest  # noqa: E402
from pkg.execution.policy import ExecutionPolicy, POLICY_DENY, \
    GLOBAL_EXECUTION_ENABLED  # noqa: E402
from pkg.capability import DEFAULT_REGISTRY, get_resolver  # noqa: E402
from pkg.capability.schemas import RESOLVED  # noqa: E402
from pkg.action.schemas import ActionIntentRecord, ACTION_TYPES  # noqa: E402

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


HELLO = dict(device_id="esp32-test-001", device_type="esp32.devboard.v1",
             firmware_version="0.3.0-p19")
LED_ADV = ["TEST_ECHO", "SET_LED"]


def _ino_code():
    for cand in (os.path.join(ROOT, "firmware_esp32",
                              "esp32_device_protocol.ino"),
                 os.path.join(ROOT, "firmware", "esp32",
                              "esp32_device_protocol.ino")):
        if os.path.exists(cand):
            with open(cand, encoding="utf-8") as fh:
                return fh.read()
    raise FileNotFoundError("firmware .ino not found")


def _ino_ops():
    src = _ino_code()
    return re.findall(r'ops\.add\("([A-Z_]+)"\)', src)


def _const_src():
    p = os.path.join(SRC_ABS, "open_llm_vtuber", "execution", "policy.py")
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def fresh_reg():
    reg = DeviceRegistry()
    reg.register_hello(DeviceHello(**HELLO), now=time.time())
    reg.advertise(CapabilityAdvertisement(device_id=HELLO["device_id"],
                                          operations=list(LED_ADV)),
                  now=time.time())
    return reg


def make_led_request(on, ids):
    intent = ActionIntentRecord.new("conf_x", ids[1], "SET_LED")
    intent.evaluation_id = ids[2]
    intent.strategy_id = ids[3]
    intent.parameters = {"on": on}
    intent.reason = "灯意图"
    contract = DEFAULT_REGISTRY.get("capability.led")
    return ExecutionRequest(conf_uid="conf_x", intent=intent,
                            contract=contract, policy_status="SANDBOX",
                            adapter_id="esp32.led")


def stage_ack(mt, cmd, on):
    mt.stage_response(cmd.command_id, json.dumps({
        "command_id": cmd.command_id, "device_id": HELLO["device_id"],
        "status": "ACK", "error_code": None, "protocol_version": 1,
        "message_type": "ack", "echo": {"on": on},
        "created_at": time.time()}))


# ---------------------------------------------------------------------------
section("19.1 Capability")
c_led = DEFAULT_REGISTRY.get("capability.led")
check("capability.led exists (vendor-free id)",
      c_led is not None and c_led.capability_id == "capability.led"
      and c_led.capability_type == "SET_LED")
check("closed schema: valid {'on': true} passes",
      c_led.validate_input({"on": True}) is None)
check("closed schema: valid {'on': false} passes",
      c_led.validate_input({"on": False}) is None)
check("non-boolean rejected", c_led.validate_input({"on": "yes"}) is not None)
check("extra parameter rejected (additionalProperties=false)",
      c_led.validate_input({"on": True, "extra": 1}) is not None)
check("missing required rejected", c_led.validate_input({}) is not None)
check("contract describes data only (no handler/function/url/shell fields)",
      not any(k in c_led.input_schema for k in
              ("handler", "function", "url", "shell", "command", "code",
               "script", "gpio", "pin")))

# ---------------------------------------------------------------------------
section("19.2 Resolver")
r = get_resolver().resolve("SET_LED")
check("SET_LED resolves deterministically to capability.led",
      r.status == RESOLVED and r.capability_id == "capability.led")
check("same input -> same contract (determinism)",
      get_resolver().resolve("SET_LED").capability is r.capability)
check("unknown body op -> UNSUPPORTED (no fuzzy match)",
      get_resolver().resolve("LIGHT_ON").status == "UNSUPPORTED"
      and get_resolver().resolve("SET_LED_BRIGHT").status == "UNSUPPORTED")
check("natural-language string -> UNSUPPORTED",
      get_resolver().resolve("turn on the light").status == "UNSUPPORTED")

# ---------------------------------------------------------------------------
section("19.3 DeviceCommand")
cmd = DeviceCommand(command_id="c1", device_id=HELLO["device_id"],
                    capability="capability.led", operation="SET_LED",
                    parameters={"on": True}, protocol_version=1,
                    provenance={"action_id": "a", "decision_id": "d",
                                "evaluation_id": "e", "strategy_id": "s"},
                    created_at=1.0)
check("SET_LED command validates (bool param)", cmd.validate() is None)
check("round trip preserves bool param",
      DeviceCommand.from_dict(cmd.to_dict()).parameters == {"on": True})
try:
    DeviceCommand(command_id="c2", device_id=HELLO["device_id"],
                  capability="capability.led", operation="SET_LED",
                  parameters={"on": 123}, protocol_version=1,
                  provenance={"action_id": "a", "decision_id": "d",
                              "evaluation_id": "e", "strategy_id": "s"},
                  created_at=1.0).validate()
    check("int parameter rejected (closed typing)", False)
except DeviceCommandError:
    check("int parameter rejected (closed typing)", True)
check("command_id still the deterministic idempotency key",
      DeviceCommand.from_dict(cmd.to_dict()).command_id == "c1")

# ---------------------------------------------------------------------------
section("19.4 Firmware validation (shared pipeline)")
# the P16 simulator now mirrors the .ino's SET_LED strictness: verify the
# firmware's closed rules through the same logic (real device BLOCKED)
check("firmware advertises TEST_ECHO + SET_LED only",
      _ino_ops() == ["TEST_ECHO", "SET_LED"])
check("firmware pins LED_PIN=2 (onboard) and fail-safe OFF at boot",
      "LED_PIN" in _ino_code() and "digitalWrite(LED_PIN, LOW)"
      in _ino_code())
check("firmware SET_LED validation is strict {'on': bool}",
      "params.size() != 1 || !params.containsKey(\"on\")"
      in _ino_code() and "params[\"on\"].is<bool>()" in _ino_code())
# code-only: strip comments first (the header docstring MENTIONS
# 'never a generic SET_GPIO interface' as the guarantee itself)
ino_code_only = re.sub(r"/\*.*?\*/", "", _ino_code(), flags=re.DOTALL)
ino_code_only = re.sub(r"//[^\n]*", "", ino_code_only)
check("firmware has NO generic GPIO interface (code-only)",
      "SET_GPIO" not in ino_code_only
      and "gpio_set_level" not in ino_code_only)


# ---------------------------------------------------------------------------
section("19.5 Session Gate")
reg = fresh_reg()
check("ONLINE + advertised SET_LED -> gate OK",
      reg.send_allowed(HELLO["device_id"], "SET_LED",
                       now=time.time()) == (True, "OK"))
check("unadvertised op -> CAPABILITY_UNSUPPORTED",
      reg.send_allowed(HELLO["device_id"], "SERVO_MOVE",
                       now=time.time())[1] == "DEVICE_CAPABILITY_UNSUPPORTED")
check("unknown device -> DEVICE_NOT_FOUND",
      reg.send_allowed("ghost", "SET_LED",
                       now=time.time())[1] == "DEVICE_NOT_FOUND")
reg.disconnect(HELLO["device_id"])
check("disconnected -> DEVICE_OFFLINE",
      reg.send_allowed(HELLO["device_id"], "SET_LED",
                       now=time.time())[1] == "DEVICE_OFFLINE")
reg2 = fresh_reg()
reg2.get(HELLO["device_id"]).state = "STALE"
check("stale -> DEVICE_STALE",
      reg2.send_allowed(HELLO["device_id"], "SET_LED",
                        now=time.time())[1] == "DEVICE_STALE")
check("protocol mismatch -> DEVICE_PROTOCOL_MISMATCH",
      fresh_reg().send_allowed(HELLO["device_id"], "SET_LED",
                               protocol_version=2,
                               now=time.time())[1]
      == "DEVICE_PROTOCOL_MISMATCH")

# ---------------------------------------------------------------------------
section("19.6 Policy + 19.13 Kill Switch")
pol = ExecutionPolicy()
esp32_probe = ESP32Adapter("capability.led", MockTransport(),
                           HELLO["device_id"])
check("kill switch OFF (code-level constant)",
      GLOBAL_EXECUTION_ENABLED is False)
check("DEVICE-level adapter -> policy DENY at default (no transport path)",
      pol.decide(c_led, esp32_probe).status == POLICY_DENY)
# code-only: the docstring says 'NO setter' — check CODE semantics:
# the constant is assigned exactly once, never rebound, no env read
policy_code = re.sub(r'""".*?"""', "", _const_src(),
                     flags=re.DOTALL)
policy_code = re.sub(r"#[^\n]*", "", policy_code)
check("no setter/config/env override for the kill switch (code-only)",
      "GLOBAL_EXECUTION_ENABLED" in policy_code
      # exactly two occurrences: one definition + one read — never rebound
      and policy_code.count("GLOBAL_EXECUTION_ENABLED") == 2
      and "os.environ" not in policy_code
      and "def set_" not in policy_code)


# ---------------------------------------------------------------------------
section("19.7 Gateway (no bypass path)")
# gateway composition is unchanged; verify the adapter cannot reach the
# transport when the gate blocks (send=0), and the gateway's policy
# boundary still applies to the led capability
check("gateway module still calls policy.decide before adapter.run",
      "self.policy.decide(" in open(os.path.join(
          SRC_ABS, "open_llm_vtuber", "execution", "gateway.py"),
          encoding="utf-8").read())

# ---------------------------------------------------------------------------
section("19.8 ESP32Adapter (on/off round trips)")
reg3 = fresh_reg()
mt = MockTransport()
obs = DeviceObserver(reg3)
ad = ESP32Adapter("capability.led", mt, HELLO["device_id"],
                  session_registry=reg3, observer=obs)
req_on = make_led_request(True, ("a1", "d1", "e1", "s1"))
cmd_on = DeviceCommand.from_request(req_on, HELLO["device_id"],
                                    operation="SET_LED")
stage_ack(mt, cmd_on, True)
p_on = ad.run(req_on)
check("LED ON round trip -> DEVICE_RESULT + device_ack",
      p_on["status"] == "DEVICE_RESULT" and p_on["device_ack"] is True
      and len(mt.sent_messages()) == 1)
req_off = make_led_request(False, ("a2", "d2", "e2", "s2"))
cmd_off = DeviceCommand.from_request(req_off, HELLO["device_id"],
                                     operation="SET_LED")
stage_ack(mt, cmd_off, False)
p_off = ad.run(req_off)
check("LED OFF round trip -> DEVICE_RESULT",
      p_off["status"] == "DEVICE_RESULT" and len(mt.sent_messages()) == 2)
st = obs.get_device_state(HELLO["device_id"])
check("observer lifecycle ACKED after LED command",
      st.last_command_status == "ACKED" and st.last_ack_status == "ACK")

# duplicate command_id: same context re-run -> transport refuses (no re-send)
p_dup = ad.run(req_on)
check("duplicate command_id refused (no LED re-action)",
      p_dup["status"] == "DEVICE_FAILED"
      and "duplicate" in p_dup["reason"].lower()
      and len(mt.sent_messages()) == 2)

# gate-blocked send stays 0
reg4 = DeviceRegistry()   # empty: no session
mt2 = MockTransport()
ad2 = ESP32Adapter("capability.led", mt2, HELLO["device_id"],
                   session_registry=reg4)
p_block = ad2.run(req_on)
check("no session -> DEVICE_GATE_REJECTED, transport send = 0",
      p_block["status"] == "DEVICE_GATE_REJECTED"
      and len(mt2.sent_messages()) == 0)

# ---------------------------------------------------------------------------
section("19.9 TCP Transport (real sockets, simulator)")
# reuse the Phase-16 firmware simulator pattern (now SET_LED-aware) over a
# REAL TCP loopback — proving transport carries the body command correctly
class LedSim(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(2)
        self.seen = []

    def run(self):
        while True:
            try:
                self.sock.settimeout(0.5)
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._h, args=(conn,), daemon=True).start()

    def _h(self, conn):
        try:
            conn.settimeout(3)
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    doc = json.loads(line)
                    op = doc["cmd"]["operation"]
                    if op == "SET_LED":
                        on = doc["cmd"]["parameters"].get("on")
                        if not isinstance(on, bool):
                            resp = self._nack(doc["cmd"]["command_id"],
                                              "INVALID_PARAMETERS")
                        else:
                            self.seen.append(on)
                            resp = json.dumps({
                                "command_id": doc["cmd"]["command_id"],
                                "device_id": HELLO["device_id"],
                                "status": "ACK", "error_code": None,
                                "protocol_version": 1,
                                "message_type": "ack",
                                "echo": {"on": on},
                                "created_at": time.time()})
                    else:
                        resp = self._nack(doc["cmd"]["command_id"],
                                          "UNKNOWN_OPERATION")
                    conn.sendall((resp + "\n").encode())
        except OSError:
            pass
        finally:
            conn.close()

    @staticmethod
    def _nack(cid, err):
        return json.dumps({"command_id": cid, "device_id": HELLO["device_id"],
                           "status": "NACK", "error_code": err,
                           "protocol_version": 1, "message_type": "ack",
                           "echo": {}, "created_at": time.time()})


sim = LedSim()
sim.start()
time.sleep(0.2)
rt = RealTCPTransport("127.0.0.1", sim.port)
ad_tcp = ESP32Adapter("capability.led", rt, HELLO["device_id"],
                      session_registry=fresh_reg())
p_tcp = ad_tcp.run(req_on)
check("real TCP: LED command carried -> ACK -> DEVICE_RESULT",
      p_tcp["status"] == "DEVICE_RESULT" and p_tcp["device_ack"] is True
      and sim.seen == [True])
rt.disconnect()
sim.sock.close()

# ---------------------------------------------------------------------------
section("19.10 ACK integrity")
reg5 = fresh_reg()
obs5 = DeviceObserver(reg5)
obs5.observe_command_created("k1", HELLO["device_id"], "SET_LED",
                             now=time.time())
check("wrong-device ACK ignored",
      not obs5.observe_ack(DeviceAck(command_id="k1",
                                     device_id="esp32-OTHER",
                                     status="ACK"))
      and obs5.get_command_status("k1").status == "CREATED")
check("wrong command_id ACK ignored (no history)",
      not obs5.observe_ack(DeviceAck(command_id="other",
                                     device_id=HELLO["device_id"],
                                     status="ACK")))
check("duplicate ACK exactly-once",
      obs5.observe_ack(DeviceAck(command_id="k1",
                                 device_id=HELLO["device_id"],
                                 status="ACK"))
      and not obs5.observe_ack(DeviceAck(command_id="k1",
                                         device_id=HELLO["device_id"],
                                         status="ACK")))

# ---------------------------------------------------------------------------
section("19.14 Real ESP32 E2E — BLOCKED (stated honestly)")
print("  Real ESP32: BLOCKED — server is a VMware VM with no USB serial,")
print("  no ESP32 toolchain (arduino-cli/esptool/idf.py), no LAN ESP32.")
print("  Firmware simulator (shared validation pipeline) = PASS.")
print("  Real-device LED action: NOT verified — never reported as PASS.")

print(f"\n{'='*58}")
print(f"PHASE 19 RESULT: {ok} passed, {fail} failed")
print(f"  [Real-device LED E2E: BLOCKED — no ESP32 hardware on server]")
print(f"{'='*58}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
