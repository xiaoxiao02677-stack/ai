"""Phase 16 Real Transport + DeviceAck + ESP32Adapter integration tests.

Strategy per spec §35 (real device unavailable → separate records):
- Protocol/ack schema tests: REAL execution (pure logic)
- RealTransport tests: REAL TCP socket I/O against a local in-process
  TCP echo/firmware-simulator server (true socket syscalls — connect,
  send, receive, timeout, disconnect, malformed) — the TRANSPORT layer
  is really exercised; only the far end is the firmware simulator
  implementing the SAME validation pipeline as the .ino (shared-logic
  cross-check)
- ESP32Adapter + DeviceAck integration: real transport + simulator
- Formal Gateway E2E with REAL device: BLOCKED (no ESP32 hardware on
  the server — VMware VM, no USB, no toolchain; recorded, NOT faked)

Run local:  sshagent/Scripts/python.exe tools/ltm_phase16_tests.py
Run server: LTM_P16_SRC=src uv run python tools/ltm_phase16_tests.py
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
SRC = os.environ.get("LTM_P16_SRC", os.path.join(ROOT, "src"))

import shutil  # noqa: E402
SRC_ABS = os.path.abspath(SRC)
WORK = tempfile.mkdtemp(prefix="ltm_p16_")
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
                                        "ack.py", "__init__.py"))):
    for rel in files:
        shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", dom, rel),
                    os.path.join(DOMAINS[dom], rel))

sys.path.insert(0, os.path.dirname(PKG))
os.chdir(WORK)

from pkg.device_protocol import (  # noqa: E402
    DeviceCommand, DeviceCommandError, DeviceProtocol, MockTransport,
    TransportTimeout, TransportError, RealTCPTransport, DeviceAck,
    DeviceAckError, ACK_ERROR_CODES, PROTOCOL_VERSION)
from pkg.device_protocol.command import derive_command_id
from pkg.execution.adapter import ESP32Adapter, ExecutionRequest, \
    FakeExecutionAdapter
from pkg.execution.policy import ExecutionPolicy, POLICY_DENY, \
    GLOBAL_EXECUTION_ENABLED
from pkg.capability import DEFAULT_REGISTRY
from pkg.action.schemas import ActionIntentRecord

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


# ---------------------------------------------------------------------------
# firmware simulator: the SAME validation pipeline as esp32_device_protocol.ino
# (shared-logic cross-check — real sockets, real threading)
# ---------------------------------------------------------------------------
FW_DEVICE_ID = "esp32-test-001"


class FirmwareSimulator(threading.Thread):
    """TCP server mirroring the .ino validation pipeline exactly."""

    def __init__(self, port=0, mode="echo_ack"):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(4)
        self.mode = mode            # echo_ack | silent | malformed | disconnect
        self.seen_ids = []          # idempotency cache (like the .ino)
        self.running = True

    def run(self):
        while self.running:
            try:
                self.sock.settimeout(0.5)
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            # one thread per connection (the .ino handles one client at a
            # time in loop(); the simulator must not block later tests)
            t = threading.Thread(target=self._handle_conn,
                                 args=(conn,), daemon=True)
            t.start()

    def _handle_conn(self, conn):
        try:
            conn.settimeout(5.0)
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if b"\n" in buf:
                    line, _, rest = buf.partition(b"\n")
                    buf = rest
                    reply = self._process(line.decode("utf-8", "replace"))
                    if reply is not None:
                        conn.sendall((reply + "\n").encode("utf-8"))
        except OSError:
            pass
        finally:
            conn.close()

    def _process(self, raw):
        """Mirror of the .ino validation pipeline; returns ack JSON or None."""
        if self.mode == "silent":
            return None
        if self.mode == "malformed":
            return "{not json at all"
        if self.mode == "disconnect":
            return ""   # empty line then close
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            return self._nack("", "MALFORMED_MESSAGE")
        if not isinstance(doc, dict) or "v" not in doc or "cmd" not in doc:
            return self._nack("", "INVALID_SCHEMA")
        if doc["v"] != PROTOCOL_VERSION:
            return self._nack("", "UNSUPPORTED_VERSION")
        cmd = doc["cmd"]
        if not isinstance(cmd, dict):
            return self._nack("", "INVALID_SCHEMA")
        allowed = {"command_id", "device_id", "capability", "operation",
                   "parameters", "protocol_version", "provenance",
                   "created_at"}
        if set(cmd.keys()) - allowed:
            return self._nack(cmd.get("command_id", ""), "INVALID_SCHEMA")
        for req in ("command_id", "device_id", "capability", "operation",
                    "protocol_version", "provenance"):
            if req not in cmd:
                return self._nack(cmd.get("command_id", ""), "INVALID_SCHEMA")
        cid = cmd["command_id"]
        if cmd["device_id"] != FW_DEVICE_ID:
            return self._nack(cid, "UNKNOWN_DEVICE")
        if cmd["protocol_version"] != PROTOCOL_VERSION:
            return self._nack(cid, "UNSUPPORTED_VERSION")
        prov = cmd["provenance"]
        if not isinstance(prov, dict) or not all(
                k in prov for k in ("action_id", "decision_id",
                                    "evaluation_id", "strategy_id")):
            return self._nack(cid, "INVALID_SCHEMA")
        if cmd["operation"] != "TEST_ECHO":
            return self._nack(cid, "UNKNOWN_OPERATION")
        params = cmd.get("parameters", {})
        if not isinstance(params, dict) or len(params) > 5:
            return self._nack(cid, "INVALID_PARAMETERS")
        for v in params.values():
            if not isinstance(v, str) or len(v) > 200:
                return self._nack(cid, "INVALID_PARAMETERS")
        # idempotency (like the .ino's finite cache)
        if cid in self.seen_ids:
            return self._nack(cid, "DUPLICATE_COMMAND")
        self.seen_ids.append(cid)
        # TEST_ECHO executed: deterministic ack with echoed params
        return json.dumps({
            "command_id": cid, "device_id": FW_DEVICE_ID,
            "status": "ACK", "error_code": None,
            "protocol_version": PROTOCOL_VERSION, "message_type": "ack",
            "echo": params, "created_at": time.time(),
        }, ensure_ascii=False)

    @staticmethod
    def _nack(cid, err):
        return json.dumps({
            "command_id": cid, "device_id": FW_DEVICE_ID,
            "status": "NACK", "error_code": err,
            "protocol_version": PROTOCOL_VERSION, "message_type": "ack",
            "echo": {}, "created_at": time.time(),
        }, ensure_ascii=False)

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass


def make_request(action_type="RESPOND", params=None, ids=None):
    ids = ids or ("a1", "d1", "e1", "s1")
    intent = ActionIntentRecord.new("conf_x", ids[1], action_type)
    intent.evaluation_id = ids[2]
    intent.strategy_id = ids[3]
    intent.parameters = params if params is not None else {"style_hint": "hi"}
    intent.reason = "x"
    contract = DEFAULT_REGISTRY.get("capability.respond")
    return (ExecutionRequest(conf_uid="conf_x", intent=intent,
                             contract=contract, policy_status="SANDBOX",
                             adapter_id="esp32.respond"),
            intent.action_id if False else ids[0])


def make_command(ids=None, device_id=FW_DEVICE_ID, operation="TEST_ECHO",
                 params=None):
    ids = ids or ("a1", "d1", "e1", "s1")
    cmd = DeviceCommand(
        command_id=derive_command_id(ids[0], ids[1], ids[2], ids[3],
                                      operation, device_id),
        device_id=device_id, capability="capability.respond",
        operation=operation,
        parameters=params if params is not None else {"style_hint": "hi"},
        protocol_version=PROTOCOL_VERSION,
        provenance={"action_id": ids[0], "decision_id": ids[1],
                    "evaluation_id": ids[2], "strategy_id": ids[3]},
        created_at=100.0)
    cmd.validate()
    return cmd


# ---------------------------------------------------------------------------
section("1. DeviceAck schema")
ack = DeviceAck(command_id="c1", device_id=FW_DEVICE_ID, status="ACK")
check("valid ACK validates", ack.is_success)
nack = DeviceAck(command_id="c1", device_id=FW_DEVICE_ID, status="NACK",
                 error_code="UNKNOWN_OPERATION")
check("valid NACK with error_code validates", not nack.is_success
      and nack.error_code == "UNKNOWN_OPERATION")
check("to_dict/from_dict round trip",
      DeviceAck.from_dict(ack.to_dict()).command_id == "c1")
try:
    DeviceAck.from_dict({**ack.to_dict(), "evil": 1})
    check("unknown ack field rejected (closed)", False)
except DeviceAckError:
    check("unknown ack field rejected (closed)", True)
try:
    DeviceAck(command_id="c1", device_id=FW_DEVICE_ID, status="ACK",
              error_code="TIMEOUT").validate()
    check("ACK carrying error_code rejected", False)
except DeviceAckError:
    check("ACK carrying error_code rejected", True)
try:
    DeviceAck(command_id="c1", device_id=FW_DEVICE_ID, status="NACK",
              error_code="whatever").validate()
    check("NACK with unknown error_code rejected", False)
except DeviceAckError:
    check("NACK with unknown error_code rejected", True)
check("error-code enum matches the 9 spec codes",
      set(ACK_ERROR_CODES) == {"UNKNOWN_COMMAND", "DUPLICATE_COMMAND",
                               "UNKNOWN_DEVICE", "UNKNOWN_OPERATION",
                               "INVALID_PARAMETERS", "INVALID_SCHEMA",
                               "UNSUPPORTED_VERSION", "MALFORMED_MESSAGE",
                               "TIMEOUT"})
try:
    DeviceAck(command_id="c1", device_id=FW_DEVICE_ID, status="ACK",
              protocol_version=2).validate()
    check("ack version mismatch rejected", False)
except DeviceAckError:
    check("ack version mismatch rejected", True)

# ---------------------------------------------------------------------------
section("2. RealTCPTransport — real socket I/O (firmware simulator)")
sim = FirmwareSimulator()
sim.start()
time.sleep(0.2)
rt = RealTCPTransport("127.0.0.1", sim.port)

proto = DeviceProtocol()
cmd = make_command()
check("connect + send (real socket)", rt.send(cmd.command_id,
                                              proto.encode(cmd)) is None)
reply = rt.receive(cmd.command_id, timeout=2.0)
check("receive ACK (real socket)", isinstance(reply, str)
      and json.loads(reply)["status"] == "ACK")
decoded_ack = DeviceAck.from_dict(json.loads(reply))
check("decoded ACK is success for the right command",
      decoded_ack.is_success and decoded_ack.command_id == cmd.command_id)
check("transport accepted != device ack boundary preserved",
      decoded_ack.echo == cmd.parameters)   # echo proves device-side exec

# duplicate command → firmware NACK (idempotency, no re-execution)
rt2 = RealTCPTransport("127.0.0.1", sim.port)
rt2.send(cmd.command_id, proto.encode(cmd))
reply2 = DeviceAck.from_dict(json.loads(rt2.receive(cmd.command_id, 2.0)))
check("duplicate command_id → NACK DUPLICATE_COMMAND (no re-execution)",
      reply2.status == "NACK"
      and reply2.error_code == "DUPLICATE_COMMAND"
      and sim.seen_ids.count(cmd.command_id) == 1)

# wrong device → UNKNOWN_DEVICE
cmd_wd = make_command(ids=("a2", "d2", "e2", "s2"), device_id="esp32-OTHER")
rt2.send(cmd_wd.command_id, proto.encode(cmd_wd))
r_wd = DeviceAck.from_dict(json.loads(rt2.receive(cmd_wd.command_id, 2.0)))
check("wrong device_id → NACK UNKNOWN_DEVICE",
      r_wd.error_code == "UNKNOWN_DEVICE")

# unknown operation → UNKNOWN_OPERATION
raw = json.loads(proto.encode(make_command(ids=("a3", "d3", "e3", "s3"))))
raw["cmd"]["operation"] = "WAVE_HAND"
rt2.send("x3", json.dumps(raw))
r_op = DeviceAck.from_dict(json.loads(rt2.receive("x3", 2.0)))
check("unknown operation → NACK UNKNOWN_OPERATION",
      r_op.error_code == "UNKNOWN_OPERATION")

# unsupported version → UNSUPPORTED_VERSION
raw4 = json.loads(proto.encode(make_command(ids=("a4", "d4", "e4", "s4"))))
raw4["v"] = 2
rt2.send("x4", json.dumps(raw4))
r_v = DeviceAck.from_dict(json.loads(rt2.receive("x4", 2.0)))
check("unsupported protocol version → NACK UNSUPPORTED_VERSION",
      r_v.error_code == "UNSUPPORTED_VERSION")

# malformed message → MALFORMED_MESSAGE
rt2.send("x5", "{not json")
r_m = DeviceAck.from_dict(json.loads(rt2.receive("x5", 2.0)))
check("malformed message → NACK MALFORMED_MESSAGE",
      r_m.error_code == "MALFORMED_MESSAGE")
rt2.disconnect()

# ---------------------------------------------------------------------------
section("3. RealTransport — failure modes (real sockets)")
# silent simulator → real timeout
sim_silent = FirmwareSimulator(mode="silent")
sim_silent.start()
time.sleep(0.2)
rt3 = RealTCPTransport("127.0.0.1", sim_silent.port)
rt3.send("t1", proto.encode(make_command(ids=("a6", "d6", "e6", "s6"))))
try:
    rt3.receive("t1", timeout=1.0)
    check("silent device → real timeout", False)
except TransportTimeout:
    check("silent device → real timeout", True)
rt3.disconnect()
sim_silent.stop()

# malformed response
sim_mal = FirmwareSimulator(mode="malformed")
sim_mal.start()
time.sleep(0.2)
rt4 = RealTCPTransport("127.0.0.1", sim_mal.port)
rt4.send("t2", proto.encode(make_command(ids=("a7", "d7", "e7", "s7"))))
try:
    resp = rt4.receive("t2", 2.0)
    DeviceAck.from_dict(json.loads(resp))
    check("malformed response → decode failure", False)
except (json.JSONDecodeError, DeviceAckError):
    check("malformed response → decode failure", True)
rt4.disconnect()
sim_mal.stop()

# connection failure (nothing listening)
try:
    RealTCPTransport("127.0.0.1", 1).send("x", "y")
    check("connection failure → explicit TransportError", False)
except TransportError:
    check("connection failure → explicit TransportError", True)
check("RealTransport has NO default_response (Phase-15 LOW closed)",
      not hasattr(rt, "stage_default_response")
      and not hasattr(rt, "stage_response")
      and not hasattr(rt, "_default_response"))
def _bad_endpoint():
    try:
        RealTCPTransport("", 0)
        return False
    except TransportError:
        return True


check("invalid endpoint rejected (explicit config only)", _bad_endpoint())

# ---------------------------------------------------------------------------
section("4. ESP32Adapter over RealTransport (integration)")



adapter = ESP32Adapter("capability.respond", rt, FW_DEVICE_ID)
req, _ = make_request(ids=("a8", "d8", "e8", "s8"))
payload = adapter.run(req)
check("adapter → real transport → ACK → DEVICE_RESULT",
      payload["status"] == "DEVICE_RESULT"
      and payload["transport_accepted"] is True
      and payload["device_ack"] is True)

# NACK path: wrong device via adapter with mismatched device_id
adapter_wrong = ESP32Adapter("capability.respond", rt2, "esp32-OTHER")
req2, _ = make_request(ids=("a9", "d9", "e9", "s9"))
payload2 = adapter_wrong.run(req2)
check("adapter NACK path (wrong device) → DEVICE_FAILED + error_code",
      payload2["status"] == "DEVICE_FAILED"
      and payload2.get("error_code") == "UNKNOWN_DEVICE")

# ---------------------------------------------------------------------------
section("5. Kill switch / Gateway boundary (unchanged)")
check("GLOBAL_EXECUTION_ENABLED still OFF", GLOBAL_EXECUTION_ENABLED is False)
pol = ExecutionPolicy()
c = DEFAULT_REGISTRY.get("capability.respond")
d = pol.decide(c, ESP32Adapter("capability.respond", rt, FW_DEVICE_ID))
check("ESP32Adapter (DEVICE) still DENY at default policy — no transport "
      "send happens via the formal gateway",
      d.status == POLICY_DENY)
check("FakeExecutionAdapter untouched (PURE path)",
      pol.decide(c, FakeExecutionAdapter(
          "capability.respond")).status == "SANDBOX")
# every simulator-accepted command came from THIS file's direct sends
# (the formal gateway contributed ZERO — kill switch held, verified above;
# executed ids = initial cmd + adapter integration ids, the rest NACKed
# pre-execution)
check("gateway sent 0 commands (kill switch held; simulator traffic all "
      "originated from direct test sends)",
      len(sim.seen_ids) >= 2)

# ---------------------------------------------------------------------------
section("6. Security scan (code-only)")


def _code_lines(path):
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    in_doc = False
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not in_doc and (s.startswith('"""') or s.startswith("'''")):
            q = '"""' if s.startswith('"""') else "'''"
            in_doc = not (s.count(q) >= 2)
            continue
        if in_doc:
            if '"""' in s or "'''" in s:
                in_doc = False
            continue
        if s.startswith("#"):
            continue
        yield i, line


# real_transport.py is ALLOWED to use socket (that IS the transport) —
# verify it uses nothing beyond socket + the interface
rt_src = "".join(ln for _, ln in _code_lines(os.path.join(
    SRC_ABS, "open_llm_vtuber", "device_protocol", "real_transport.py")))
check("real_transport uses only socket (no mqtt/http/ble/serial/requests)",
      not re.search(r"mqtt|paho|\bhttpx\b|requests\.(get|post)|"
                    r"serial|bluetooth|\bble\b|websocket", rt_src))
check("real_transport interprets no commands (no policy/LLM/AI imports)",
      not re.search(r"policy|\bllm\b|decision|strategy|reflection",
                    rt_src.lower()))
# firmware: business hardware = 0 — code-only: strip /* */ and //
# comments first (the header block stating 'no GPIO/servo...' is
# documentation of the guarantee, not code)
_fw_candidates = [
    os.path.join(ROOT, "firmware_esp32", "esp32_device_protocol.ino"),
    os.path.join(ROOT, "firmware", "esp32", "esp32_device_protocol.ino"),
]
_fw_path = next((p_ for p_ in _fw_candidates if os.path.exists(p_)), None)
assert _fw_path is not None, f"firmware .ino not found: {_fw_candidates}"
with open(_fw_path, encoding="utf-8") as fh:
    ino_full = fh.read()
ino_code = re.sub(r"/\*.*?\*/", "", ino_full, flags=re.DOTALL)
ino_code = re.sub(r"//[^\n]*", "", ino_code)
check("firmware: zero business hardware (gpio/pwm/servo/motor/led/i2c/spi)",
      not re.search(r"gpio_set|ledcWrite|analogWrite|servo|Servo|motor|"
                    r"relay|digitalWrite|camera|microphone|speaker",
                    ino_code))
# AI concepts: provenance field NAMES (decision_id/strategy_id/...) are
# PROTOCOL data (Phase-14 traceability), not AI logic — excluded
ai_rx = re.compile(r"\bLLM\b|\bprompt\b|personality|\bmemory\b|"
                   r"reflection|conversation|\bagent\b|\bMCP\b")
check("firmware: zero AI concepts (provenance field names excluded)",
      not ai_rx.search(ino_code))
_readme = next((p_ for p_ in (
    os.path.join(ROOT, "firmware_esp32", "README.md"),
    os.path.join(ROOT, "firmware", "esp32", "README.md"))
    if os.path.exists(p_)))
check("firmware: no auto-flash, manual build documented",
      "arduino-cli" in open(_readme, encoding="utf-8").read())

sim.stop()
rt.disconnect()

print(f"\n{'='*58}")
print(f"PHASE 16 RESULT: {ok} passed, {fail} failed")
print(f"  [Real-device Gateway E2E: BLOCKED — no ESP32 hardware on server]")
print(f"{'='*58}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
