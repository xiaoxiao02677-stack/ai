"""Phase 15 Device Command + Protocol + Transport + ESP32Adapter tests.

Covers (spec §32): DeviceCommand (valid/invalid/closed schema/unknown
field/unknown operation/invalid parameter/invalid provenance/invalid
device) / command_id (deterministic same/different context) /
Protocol (encode/decode/round trip/invalid version/invalid payload/
malformed) / Transport (send/receive/timeout/unknown message/
duplicate/malformed response) / ESP32Adapter (valid request, policy
denied by default, kill switch off, device command generated,
protocol encoding, mock transport, result propagation, deterministic
id) / boundary (no LLM path, no Gateway/Policy bypass, no real
transport/device).

Run local:  sshagent/Scripts/python.exe tools/ltm_phase15_tests.py
Run server: LTM_DEV_SRC=src uv run python tools/ltm_phase15_tests.py
"""
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_DEV_SRC", os.path.join(ROOT, "src"))

import shutil  # noqa: E402
SRC_ABS = os.path.abspath(SRC)
WORK = tempfile.mkdtemp(prefix="ltm_p15_")
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
                                        "ack.py", "session.py", "__init__.py"))):
    for rel in files:
        shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", dom, rel),
                    os.path.join(DOMAINS[dom], rel))

sys.path.insert(0, os.path.dirname(PKG))
os.chdir(WORK)

from pkg.device_protocol import (  # noqa: E402
    DeviceCommand, DeviceCommandError, DeviceProtocol, MockTransport,
    TransportTimeout, TransportError, derive_command_id, PROTOCOL_VERSION)
from pkg.execution.adapter import (ESP32Adapter, ExecutionRequest,  # noqa: E402
                                   FakeExecutionAdapter, AdapterRegistry)
from pkg.execution.policy import (ExecutionPolicy, POLICY_DENY,  # noqa: E402
                                  POLICY_SANDBOX, GLOBAL_EXECUTION_ENABLED)
from pkg.capability import DEFAULT_REGISTRY  # noqa: E402
from pkg.action.schemas import ActionIntentRecord  # noqa: E402

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


def base_cmd(**overrides):
    d = dict(command_id="c1", device_id="device.body",
             capability="capability.respond", operation="TEST_ECHO",
             parameters={}, protocol_version=PROTOCOL_VERSION,
             provenance={"action_id": "a", "decision_id": "d",
                         "evaluation_id": "e", "strategy_id": "s"},
             created_at=100.0)
    d.update(overrides)
    return DeviceCommand.from_dict(d)


def make_request(action_type="RESPOND", params=None):
    intent = ActionIntentRecord.new("conf_x", "dec1", action_type)
    intent.evaluation_id = "ev1"
    intent.strategy_id = "st1"
    intent.parameters = params if params is not None else {"style_hint": "hi"}
    intent.reason = "x"
    contract = DEFAULT_REGISTRY.get("capability.respond")
    return ExecutionRequest(conf_uid="conf_x", intent=intent, contract=contract,
                            policy_status="SANDBOX", adapter_id="esp32.respond")


# ---------------------------------------------------------------------------
section("1. DeviceCommand closed schema")
cmd = base_cmd()
check("valid command validates", cmd.validate() is None)
check("to_dict/from_dict round trip",
      DeviceCommand.from_dict(cmd.to_dict()).command_id == "c1")
try:
    DeviceCommand.from_dict({**cmd.to_dict(), "evil_field": "x"})
    check("unknown top-level field rejected (closed schema)", False)
except DeviceCommandError:
    check("unknown top-level field rejected (closed schema)", True)
try:
    base_cmd(operation="WAVE_HAND")
    check("unknown operation rejected", False)
except DeviceCommandError as e:
    check("unknown operation rejected", True)
try:
    base_cmd(protocol_version=2)
    check("unknown protocol version rejected (no auto-upgrade)", False)
except DeviceCommandError:
    check("unknown protocol version rejected (no auto-upgrade)", True)
try:
    base_cmd(device_id="192.168.1.5")
    check("address-like device_id rejected (business identity only)", False)
except DeviceCommandError:
    check("address-like device_id rejected (business identity only)", True)
try:
    base_cmd(parameters={"x": 123})
    check("non-string parameter rejected", False)
except DeviceCommandError:
    check("non-string parameter rejected", True)
try:
    base_cmd(provenance={"action_id": "a"})   # incomplete provenance
    check("incomplete provenance rejected", False)
except DeviceCommandError:
    check("incomplete provenance rejected", True)
try:
    base_cmd(provenance={"action_id": "a", "decision_id": "d",
                         "evaluation_id": "e", "strategy_id": "s",
                         "extra": "x"})
    check("unknown provenance key rejected (closed)", False)
except DeviceCommandError:
    check("unknown provenance key rejected (closed)", True)
try:
    base_cmd(parameters={"k": "x" * 201})
    check("overlong parameter value rejected", False)
except DeviceCommandError:
    check("overlong parameter value rejected", True)

# ---------------------------------------------------------------------------
section("2. command_id determinism")
c_a = derive_command_id("a", "d", "e", "s", "TEST_ECHO", "device.body")
c_b = derive_command_id("a", "d", "e", "s", "TEST_ECHO", "device.body")
c_c = derive_command_id("a2", "d", "e", "s", "TEST_ECHO", "device.body")
check("same context -> same command_id", c_a == c_b)
check("different context -> different command_id", c_a != c_c)
req = make_request()
cmd1 = DeviceCommand.from_request(req, "device.body")
cmd2 = DeviceCommand.from_request(req, "device.body")
check("from_request deterministic (same request -> same id)",
      cmd1.command_id == cmd2.command_id)
req2 = make_request(params={"style_hint": "different"})
cmd3 = DeviceCommand.from_request(req2, "device.body")
check("different parameters -> different command_id",
      cmd3.command_id != cmd1.command_id)

# ---------------------------------------------------------------------------
section("3. DeviceProtocol encode/decode")
proto = DeviceProtocol()
msg = proto.encode(cmd)
rt = proto.decode(msg)
check("round trip preserves all fields",
      rt.command_id == cmd.command_id
      and rt.operation == cmd.operation
      and rt.parameters == cmd.parameters
      and rt.provenance == cmd.provenance
      and rt.device_id == cmd.device_id)
check("encode is deterministic",
      proto.encode(cmd) == proto.encode(DeviceCommand.from_dict(cmd.to_dict())))
for bad_msg in ("{not json", '"just a string"', "{}", json.dumps({"v": 1}),
                json.dumps({"v": 99, "cmd": cmd.to_dict()})):
    try:
        proto.decode(bad_msg)
        check(f"malformed message rejected ({bad_msg[:20]}…)", False)
    except DeviceCommandError:
        check(f"malformed message rejected ({bad_msg[:20]}…)", True)
tampered = json.loads(proto.encode(cmd))
tampered["cmd"]["operation"] = "WAVE_HAND"
try:
    proto.decode(json.dumps(tampered))
    check("tampered operation rejected on decode", False)
except DeviceCommandError:
    check("tampered operation rejected on decode", True)

# ---------------------------------------------------------------------------
section("4. MockTransport")
mt = MockTransport()
mt.send("m1", "hello")
check("send success + stored", mt.was_sent("m1")
      and mt.sent_messages()["m1"] == "hello")
try:
    mt.send("m1", "again")
    check("duplicate message_id rejected", False)
except TransportError:
    check("duplicate message_id rejected", True)
try:
    mt.receive("m1")
    check("unstaged receive -> timeout", False)
except TransportTimeout:
    check("unstaged receive -> timeout", True)
try:
    mt.receive("ghost")
    check("unknown message_id -> timeout", False)
except TransportTimeout:
    check("unknown message_id -> timeout", True)
mt.stage_response("m1", "reply")
check("staged response received", mt.receive("m1") == "reply")
mt.stage_default_response("default")
check("default response for unstaged ids", mt.receive("other") == "default")
mt.stage_default_response(None)

# ---------------------------------------------------------------------------
section("5. ESP32Adapter skeleton")
pol = ExecutionPolicy()
esp32 = ESP32Adapter("capability.respond", mt, "device.body")
c_resp = DEFAULT_REGISTRY.get("capability.respond")
check("ESP32Adapter declares DEVICE level", esp32.side_effect_level == "DEVICE")
check("global kill switch still OFF", GLOBAL_EXECUTION_ENABLED is False)
d = pol.decide(c_resp, esp32)
check("policy: DEVICE adapter with switch off -> DENY",
      d.status == POLICY_DENY and "KILL SWITCH" in d.reason)
check("FakeExecutionAdapter still PURE -> SANDBOX (coexists)",
      pol.decide(c_resp, FakeExecutionAdapter(
          "capability.respond")).status == POLICY_SANDBOX)

# full adapter chain: command -> encode -> mock send -> staged echo -> result
mt2 = MockTransport()
esp2 = ESP32Adapter("capability.respond", mt2, "device.body")
request = make_request()
command = DeviceCommand.from_request(request, "device.body")
mt2.stage_response(command.command_id, proto.encode(command))
payload = esp2.run(request)
check("adapter produced a DeviceCommand (validated)",
      command.operation == "TEST_ECHO"
      and command.provenance["action_id"] == request.action_id)
check("adapter chain -> DEVICE_RESULT via mock transport",
      payload["status"] == "DEVICE_RESULT"
      and payload["transport_accepted"] is True
      and payload["device_ack"] is True
      and payload["command_id"] == command.command_id)
check("transport accepted != device executed boundary expressed",
      "transport_accepted" in payload and "device_ack" in payload)

# transport timeout path
mt3 = MockTransport()
esp3 = ESP32Adapter("capability.respond", mt3, "device.body")
p_timeout = esp3.run(request)
check("no response -> DEVICE_TIMEOUT (never fake success)",
      p_timeout["status"] == "DEVICE_TIMEOUT"
      and p_timeout["simulated"] is False)

# invalid response path
mt4 = MockTransport()
esp4 = ESP32Adapter("capability.respond", mt4, "device.body")
mt4.stage_response(command.command_id, "{not json")
p_bad = esp4.run(request)
check("malformed device response -> DEVICE_FAILED",
      p_bad["status"] == "DEVICE_FAILED")

# duplicate send (idempotency surfacing)
mt5 = MockTransport()
esp5 = ESP32Adapter("capability.respond", mt5, "device.body")
mt5.stage_response(command.command_id, proto.encode(command))
esp5.run(request)
p_dup = esp5.run(request)   # same context -> same command_id -> dup send
check("re-execution surfaces duplicate refusal (idempotency visible)",
      p_dup["status"] == "DEVICE_FAILED" and "transport refused" in p_dup["reason"])

# ---------------------------------------------------------------------------
section("6. Boundary scans")


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


DEV_DIR = os.path.join(SRC_ABS, "open_llm_vtuber", "device_protocol")
REAL_IO_RX = re.compile(
    r"socket\.|requests\.(get|post|put|delete)|\bhttpx\b|aiohttp|urllib"
    r"|websocket|mqtt|serial|bluetooth|\bble\b|gpio|esp32\.|idf\.py"
    r"|subprocess|os\.system|eval\(|exec\(")
# Phase-15 boundary scope: the zero-I/O guarantee covered the Phase-15
# package files; real_transport.py/ack.py are Phase-16 additions covered
# by the phase-16 suite's own (socket-allowed-for-real_transport) scan
P15_SCOPE = {"__init__.py", "command.py", "protocol.py", "transport.py"}
for fn in sorted(os.listdir(DEV_DIR)):
    if not fn.endswith(".py") or fn not in P15_SCOPE:
        continue
    hits = [ln.strip()[:70] for _, ln in _code_lines(os.path.join(DEV_DIR, fn))
            if REAL_IO_RX.search(ln)]
    check(f"device_protocol/{fn}: zero real I/O code", not hits, str(hits[:2]))
# adapter holds only the Transport interface (no real client) —
# code-only scan via _code_lines (docstring words like "requests yield"
# are documentation, not imports or calls)
with open(os.path.join(SRC_ABS, "open_llm_vtuber", "execution",
                       "adapter.py"), encoding="utf-8") as fh:
    pass
adp_code = "".join(ln for _, ln in _code_lines(os.path.join(
    SRC_ABS, "open_llm_vtuber", "execution", "adapter.py")))
check("ESP32Adapter holds no real network client (code-only)",
      not re.search(r"\brequests\b|socket\.|\bhttpx\b|mqtt|"
                    r"serial\.Serial|\bble\b", adp_code))
# no LLM in device_protocol
for fn in sorted(os.listdir(DEV_DIR)):
    if not fn.endswith(".py"):
        continue
    code = "".join(ln for _, ln in _code_lines(os.path.join(DEV_DIR, fn)))
    check(f"device_protocol/{fn}: no LLM usage",
          not re.search(r"\bllm\b|chat_completion", code.lower()))
# device_protocol knows nothing about AI domains
for fn in sorted(os.listdir(DEV_DIR)):
    if not fn.endswith(".py"):
        continue
    with open(os.path.join(DEV_DIR, fn), encoding="utf-8") as fh:
        src_text = fh.read()
    check(f"device_protocol/{fn}: no AI-layer imports",
          not re.search(r"from \.\.(experience|reflection|lesson|strategy|"
                        r"evaluation|decision|action|execution)", src_text))

print(f"\n{'='*54}")
print(f"PHASE 15 RESULT: {ok} passed, {fail} failed")
print(f"{'='*54}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
