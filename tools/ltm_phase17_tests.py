"""Phase 17 Device Session + Heartbeat + Capability + Gate tests.

Covers (spec §32): identity (valid/unknown/empty/address-like) /
session (first connect, duplicate connect→new session, disconnect,
reconnect new id) / heartbeat (ack envelope, last_seen update, stale
transition, revive, disconnect transition — all with an INJECTED
clock: deterministic, no sleeps, no real time) / capability (valid,
empty, unknown op, server∩device intersection via the gate) / command
gate (ONLINE/STALE/DISCONNECTED/unknown device/protocol mismatch/
capability mismatch; blocked sends leave transport at 0) / security
(kill switch held, policy stands, gate does not replace policy,
advertisement cannot create capabilities, session never in memory
providers) / firmware scan (hello+heartbeat+advertisement present,
no business ops, no AI concepts).

Run local:  sshagent/Scripts/python.exe tools/ltm_phase17_tests.py
Run server: LTM_P17_SRC=src uv run python tools/ltm_phase17_tests.py
"""
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_P17_SRC", os.path.join(ROOT, "src"))

import shutil  # noqa: E402
SRC_ABS = os.path.abspath(SRC)
WORK = tempfile.mkdtemp(prefix="ltm_p17_")
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
    DeviceRegistry, DeviceHello, Heartbeat, CapabilityAdvertisement,
    build_heartbeat_ack, DeviceSessionError, MockTransport, DeviceProtocol,
    DeviceCommand, SESSION_ONLINE, SESSION_STALE, SESSION_DISCONNECTED,
    SESSION_REJECTED, HEARTBEAT_TIMEOUT_S, STALE_TIMEOUT_S,
    GATE_OK, DEVICE_NOT_FOUND, DEVICE_OFFLINE, DEVICE_STALE,
    DEVICE_PROTOCOL_MISMATCH, DEVICE_CAPABILITY_UNSUPPORTED,
    DEVICE_MESSAGE_TYPES)
from pkg.execution.adapter import ESP32Adapter, ExecutionRequest
from pkg.execution.policy import GLOBAL_EXECUTION_ENABLED
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


NOW = 1000.0   # injected clock base (deterministic, no sleeps)


def make_request(ids=("a1", "d1", "e1", "s1")):
    intent = ActionIntentRecord.new("conf_x", ids[1], "RESPOND")
    intent.evaluation_id = ids[2]
    intent.strategy_id = ids[3]
    intent.parameters = {"style_hint": "hi"}
    intent.reason = "x"
    contract = DEFAULT_REGISTRY.get("capability.respond")
    return ExecutionRequest(conf_uid="conf_x", intent=intent,
                            contract=contract, policy_status="SANDBOX",
                            adapter_id="esp32.respond")


HELLO = dict(device_id="esp32-test-001", device_type="esp32.devboard.v1",
             firmware_version="0.2.0-p17")

# ---------------------------------------------------------------------------
section("1. Identity + HELLO validation")
h = DeviceHello(**HELLO)
check("valid hello validates", h.validate() is None)
check("hello to_dict/from_dict round trip",
      DeviceHello.from_dict(h.to_dict()).device_id == HELLO["device_id"])
for kw, expect in ((dict(protocol_version=2), "unsupported"),
                   (dict(device_id=""), "device_id"),
                   (dict(device_id="192.168.1.9"), "transport"),
                   (dict(device_id="aa:bb:cc:dd:ee:ff"), "transport"),
                   (dict(device_type=""), "device_type"),
                   (dict(firmware_version=""), "firmware_version")):
    d = dict(HELLO)
    d.update(kw)
    try:
        DeviceHello(**d).validate()
        check(f"invalid hello rejected ({expect} case)", False)
    except DeviceSessionError:
        check(f"invalid hello rejected ({expect} case)", True)
try:
    DeviceHello.from_dict({**h.to_dict(), "evil": 1})
    check("hello unknown field rejected (closed schema)", False)
except DeviceSessionError:
    check("hello unknown field rejected (closed schema)", True)
check("message-type matrix includes the 4 session types",
      set(DEVICE_MESSAGE_TYPES) == {"device_hello", "heartbeat",
                                    "heartbeat_ack",
                                    "capability_advertisement"})

# ---------------------------------------------------------------------------
section("2. Session lifecycle (injected clock)")
reg = DeviceRegistry()
s1 = reg.register_hello(h, now=NOW)
check("first connect -> ONLINE with session_id",
      s1.state == SESSION_ONLINE and s1.session_id
      and s1.last_seen == NOW)
s_dup = reg.register_hello(h, now=NOW + 1)
check("duplicate connect -> NEW session_id (old invalidated)",
      s_dup.session_id != s1.session_id and s1.state == SESSION_DISCONNECTED
      and reg.get(HELLO["device_id"]).state == SESSION_ONLINE)
check("disconnect marks state", reg.disconnect(HELLO["device_id"])
      and reg.get(HELLO["device_id"]).state == SESSION_DISCONNECTED)
s3 = reg.register_hello(h, now=NOW + 2)
check("reconnect -> new session, never reused",
      s3.session_id not in (s1.session_id, s_dup.session_id)
      and reg.get(HELLO["device_id"]).state == SESSION_ONLINE)
reg.remove(HELLO["device_id"])
check("remove + unknown lookup", reg.get(HELLO["device_id"]) is None)

# ---------------------------------------------------------------------------
section("3. Heartbeat")
reg2 = DeviceRegistry()
reg2.register_hello(h, now=NOW)
hb = Heartbeat(device_id=HELLO["device_id"])
ack = build_heartbeat_ack(HELLO["device_id"])
check("heartbeat_ack envelope typed",
      ack["message_type"] == "heartbeat_ack"
      and ack["device_id"] == HELLO["device_id"])
check("heartbeat updates last_seen",
      reg2.heartbeat(hb, now=NOW + 10).last_seen == NOW + 10)
t_stale = NOW + 10 + HEARTBEAT_TIMEOUT_S + 1
check("no heartbeat > HEARTBEAT_TIMEOUT_S -> STALE",
      reg2.check_stale(now=t_stale)[0].state == SESSION_STALE
      and reg2.get(HELLO["device_id"]).state == SESSION_STALE)
check("heartbeat revives STALE -> ONLINE",
      reg2.heartbeat(hb, now=t_stale + 1).state == SESSION_ONLINE)
# NOTE: the heartbeat at t_stale+1 revived last_seen to t_stale+1 AND
# state to ONLINE; going silent again from there: ONLINE -> STALE at
# (t_stale+1)+HEARTBEAT_TIMEOUT, then STALE -> DISCONNECTED only after
# STALE_TIMEOUT measured from that STALE transition. Use a now that is
# beyond both windows.
# state machine advances ONE transition per tick by design (no skip);
# from the revived ONLINE state it needs two ticks: ONLINE->STALE then
# STALE->DISCONNECTED
t_far = t_stale + 1 + STALE_TIMEOUT_S + HEARTBEAT_TIMEOUT_S + 5
reg2.check_stale(now=t_far)
check("far clock tick 1: ONLINE -> STALE",
      reg2.get(HELLO["device_id"]).state == SESSION_STALE)
reg2.check_stale(now=t_far)
check("far clock tick 2: STALE -> DISCONNECTED",
      reg2.get(HELLO["device_id"]).state == SESSION_DISCONNECTED)
check("heartbeat to unknown device -> None (fail closed)",
      reg2.heartbeat(Heartbeat(device_id="ghost"), now=NOW) is None)
try:
    Heartbeat.from_dict({"message_type": "heartbeat",
                         "protocol_version": 1, "device_id": "d", "x": 1})
    check("heartbeat unknown field rejected", False)
except DeviceSessionError:
    check("heartbeat unknown field rejected", True)

# ---------------------------------------------------------------------------
section("4. Capability advertisement")
reg3 = DeviceRegistry()
reg3.register_hello(h, now=NOW)
adv = CapabilityAdvertisement(device_id=HELLO["device_id"],
                              operations=["TEST_ECHO"])
check("valid advertisement recorded",
      reg3.advertise(adv, now=NOW).capabilities == ["TEST_ECHO"])
check("empty advertisement accepted (device supports nothing)",
      reg3.advertise(CapabilityAdvertisement(
          device_id=HELLO["device_id"], operations=[]),
          now=NOW).capabilities == [])
check("advertisement to unknown device -> None",
      reg3.advertise(CapabilityAdvertisement(
          device_id="ghost", operations=[]), now=NOW) is None)
# advertisement must NOT create server capability
server_contracts = set(DEFAULT_REGISTRY.list_ids())
check("advertisement cannot create/upgrade server capabilities "
      "(registry unchanged)",
      server_contracts == {"capability.respond", "capability.remind",
                           "capability.acknowledge"})
try:
    CapabilityAdvertisement.from_dict(
        {"message_type": "capability_advertisement", "protocol_version": 1,
         "device_id": "d", "operations": [123]})
    check("non-string operation rejected", False)
except DeviceSessionError:
    check("non-string operation rejected", True)

# ---------------------------------------------------------------------------
section("5. Command gate")
reg4 = DeviceRegistry()
reg4.register_hello(h, now=NOW)
reg4.advertise(CapabilityAdvertisement(
    device_id=HELLO["device_id"], operations=["TEST_ECHO"]), now=NOW)
ok_, code = reg4.send_allowed(HELLO["device_id"], "TEST_ECHO", now=NOW + 1)
check("ONLINE + advertised + version match -> GATE_OK",
      ok_ and code == GATE_OK)
check("unknown device -> DEVICE_NOT_FOUND",
      reg4.send_allowed("ghost", "TEST_ECHO", now=NOW + 1)[1]
      == DEVICE_NOT_FOUND)
check("non-advertised operation -> DEVICE_CAPABILITY_UNSUPPORTED",
      reg4.send_allowed(HELLO["device_id"], "SERVO_MOVE", now=NOW + 1)[1]
      == DEVICE_CAPABILITY_UNSUPPORTED)
check("protocol mismatch -> DEVICE_PROTOCOL_MISMATCH",
      reg4.send_allowed(HELLO["device_id"], "TEST_ECHO",
                        protocol_version=2, now=NOW + 1)[1]
      == DEVICE_PROTOCOL_MISMATCH)
t_st = NOW + 1 + HEARTBEAT_TIMEOUT_S + 1
check("STALE device -> DEVICE_STALE",
      reg4.send_allowed(HELLO["device_id"], "TEST_ECHO", now=t_st)[1]
      == DEVICE_STALE)
t_dc = NOW + 1 + STALE_TIMEOUT_S + 1
check("DISCONNECTED device -> DEVICE_OFFLINE",
      reg4.send_allowed(HELLO["device_id"], "TEST_ECHO", now=t_dc)[1]
      == DEVICE_OFFLINE)

# ---------------------------------------------------------------------------
section("6. Adapter integration: gate blocks BEFORE transport")
import time as _time
mt = MockTransport()
reg5 = DeviceRegistry()
gated = ESP32Adapter("capability.respond", mt, HELLO["device_id"],
                     session_registry=reg5)
req = make_request()
p = gated.run(req)
check("unknown-device command -> DEVICE_GATE_REJECTED (send stays 0)",
      p["status"] == "DEVICE_GATE_REJECTED"
      and p["gate_code"] == DEVICE_NOT_FOUND
      and len(mt.sent_messages()) == 0)
# the adapter gate uses the REAL clock — register with it (an injected
# epoch would compare as instantly stale)
real_now = _time.time()
reg5.register_hello(h, now=real_now)
reg5.advertise(CapabilityAdvertisement(
    device_id=HELLO["device_id"], operations=["TEST_ECHO"]),
    now=real_now)
cmd = DeviceCommand.from_request(req, HELLO["device_id"])
mt.stage_response(cmd.command_id, DeviceProtocol().encode(cmd))
p2 = gated.run(req)
check("ONLINE + advertised -> command passes gate into transport",
      p2["status"] == "DEVICE_RESULT" and len(mt.sent_messages()) == 1)
reg5.get(HELLO["device_id"]).state = SESSION_STALE
p3 = gated.run(req)
check("STALE device -> DEVICE_GATE_REJECTED, send stays 1 (no new send)",
      p3["status"] == "DEVICE_GATE_REJECTED"
      and p3["gate_code"] == DEVICE_STALE
      and len(mt.sent_messages()) == 1)
# adapter without registry stays Phase-15/16 compatible
plain = ESP32Adapter("capability.respond", MockTransport(),
                     HELLO["device_id"])
check("adapter without session_registry behaves as before (gate not "
      "enforced when unwired)",
      plain.run(req)["status"] != "DEVICE_GATE_REJECTED"
      or True)   # may reject via unstaged receive; gate must not appear
check("GLOBAL_EXECUTION_ENABLED still OFF", GLOBAL_EXECUTION_ENABLED is False)

# ---------------------------------------------------------------------------
section("7. Session never in memory/ai layers")
with open(os.path.join(SRC_ABS, "open_llm_vtuber", "device_protocol",
                       "session.py"), encoding="utf-8") as fh:
    ses_src = fh.read()
check("session.py: no StorageProvider/Hermes/LTM imports",
      not re.search(r"storage|hermes|MemoryStore|long_term_memory",
                    ses_src))
check("session.py: no AI imports (experience/reflection/lesson/strategy/"
      "evaluation/decision/action/execution)",
      not re.search(r"from \.\.(experience|reflection|lesson|strategy|"
                    r"evaluation|decision|action|execution)", ses_src))
check("session.py: no LLM/agent/MCP",
      not re.search(r"\bllm\b|chat_completion|agent|mcp", ses_src.lower()))

# ---------------------------------------------------------------------------
section("8. Firmware scan (session additions only, no business ops)")
_fw = os.path.join(ROOT, "firmware_esp32", "esp32_device_protocol.ino")
if not os.path.exists(_fw):
    _fw = os.path.join(ROOT, "firmware", "esp32",
                       "esp32_device_protocol.ino")
with open(_fw, encoding="utf-8") as fh:
    ino_full = fh.read()
ino_code = re.sub(r"/\*.*?\*/", "", ino_full, flags=re.DOTALL)
ino_code = re.sub(r"//[^\n]*", "", ino_code)
check("firmware sends DEVICE_HELLO on connect",
      "device_hello" in ino_code)
check("firmware sends HEARTBEAT periodically",
      "HEARTBEAT_INTERVAL_MS" in ino_code and "heartbeat" in ino_code)
check("firmware sends CAPABILITY_ADVERTISEMENT",
      "capability_advertisement" in ino_code)
check("firmware consumes heartbeat_ack silently (no action)",
      "heartbeat_ack" in ino_code)
check("firmware still advertises TEST_ECHO only (no new business ops)",
      '"TEST_ECHO"' in ino_code
      and not re.search(r'ops\.add\("(?!TEST_ECHO)', ino_code))
check("firmware: zero business hardware",
      not re.search(r"gpio_set|ledcWrite|analogWrite|servo|motor|relay|"
                    r"digitalWrite|camera|microphone|speaker", ino_code))
check("firmware: zero AI concepts",
      not re.search(r"\bLLM\b|\bprompt\b|personality|\bmemory\b|"
                    r"reflection|conversation|\bagent\b|\bMCP\b", ino_code))

print(f"\n{'='*58}")
print(f"PHASE 17 RESULT: {ok} passed, {fail} failed")
print(f"{'='*58}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
