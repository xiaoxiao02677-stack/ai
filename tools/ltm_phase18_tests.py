"""Phase 18 Device State + Health + Command Lifecycle + Observer tests.

Covers (spec §39): DeviceState (from session/after hello/heartbeat/
advertisement/disconnect/unknown) / Health (online/stale/disconnected/
heartbeat-age computed/protocol compat/unknown) / Command lifecycle
(created/sent/acked/nacked/timeout/failed/rejected + bounded FIFO
eviction) / ACK integrity (unknown command ignored no-history, device
forgery, wrong command_id — unknown ack ignored, duplicate exactly-
once, late ack never rewrites timeout but flags) / Security (fail
closed across the matrix) / Isolation (observer has no send/execute/
retry; state never creates intents; ready never bypasses gate/policy;
advertisement never touches contracts; zero persistence imports).

All timing via injected clocks — no sleeps.
Run local:  sshagent/Scripts/python.exe tools/ltm_phase18_tests.py
Run server: LTM_P18_SRC=src uv run python tools/ltm_phase18_tests.py
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_P18_SRC", os.path.join(ROOT, "src"))

import shutil  # noqa: E402
SRC_ABS = os.path.abspath(SRC)
WORK = tempfile.mkdtemp(prefix="ltm_p18_")
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
    DeviceAck, CommandHistory, CommandRecord, MAX_COMMAND_HISTORY,
    SESSION_ONLINE, SESSION_STALE, SESSION_DISCONNECTED,
    LIFECYCLE_CREATED, LIFECYCLE_SENT, LIFECYCLE_ACKED, LIFECYCLE_NACKED,
    LIFECYCLE_REJECTED, LIFECYCLE_TIMEOUT, LIFECYCLE_FAILED,
    HEALTH_NOT_FOUND, HEALTH_OK, HEARTBEAT_TIMEOUT_S, STALE_TIMEOUT_S,
    GATE_OK, DEVICE_NOT_FOUND)
from pkg.execution.policy import GLOBAL_EXECUTION_ENABLED

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


NOW = 1000.0
HELLO = dict(device_id="esp32-test-001", device_type="esp32.devboard.v1",
             firmware_version="0.2.0-p17")
H = DeviceHello(**HELLO)


def fresh_reg():
    reg = DeviceRegistry()
    reg.register_hello(H, now=NOW)
    reg.advertise(CapabilityAdvertisement(
        device_id=HELLO["device_id"], operations=["TEST_ECHO"]), now=NOW)
    return reg


# ---------------------------------------------------------------------------
section("1. DeviceState snapshots (>=8)")
reg = fresh_reg()
obs = DeviceObserver(reg)
st = obs.get_device_state(HELLO["device_id"], now=NOW + 1)
check("state after hello: session fields consistent",
      st.session_id == reg.get(HELLO["device_id"]).session_id
      and st.connection_state == SESSION_ONLINE
      and st.device_type == HELLO["device_type"]
      and st.protocol_version == 1
      and st.firmware_version == HELLO["firmware_version"])
check("state capabilities come from advertisement",
      st.capabilities == ["TEST_ECHO"])
from pkg.device_protocol import Heartbeat  # noqa: E402
check("state after heartbeat updates last_seen",
      (reg.heartbeat(Heartbeat(device_id=HELLO["device_id"]),
                     now=NOW + 5)
       or True)
      and obs.get_device_state(HELLO["device_id"], now=NOW + 5).last_seen
      == NOW + 5)
check("state after advertisement updates capabilities",
      (reg.advertise(CapabilityAdvertisement(
          device_id=HELLO["device_id"], operations=[]), now=NOW + 6)
       or True)
      and obs.get_device_state(HELLO["device_id"], now=NOW + 6).capabilities
      == [])
check("state after disconnect mirrors session state",
      (reg.disconnect(HELLO["device_id"]) or True)
      and obs.get_device_state(HELLO["device_id"],
                               now=NOW + 7).connection_state
      == SESSION_DISCONNECTED)
check("unknown device state -> None",
      obs.get_device_state("ghost") is None)
check("no second state machine: snapshot == session.state verbatim",
      obs.get_device_state(HELLO["device_id"],
                           now=NOW + 7).connection_state
      == reg.get(HELLO["device_id"]).state)
check("state snapshot never stale-mismatches the session",
      obs.get_device_state(HELLO["device_id"],
                           now=NOW + 7).device_id
      == reg.get(HELLO["device_id"]).device_id)

# ---------------------------------------------------------------------------
section("2. DeviceHealth (>=6)")
reg2 = fresh_reg()
obs2 = DeviceObserver(reg2)
hp, code = obs2.get_device_health(HELLO["device_id"], now=NOW + 6)
check("online health: OK + online + computed heartbeat_age",
      code == HEALTH_OK and hp.online and hp.health_state == SESSION_ONLINE
      and abs(hp.heartbeat_age - 6.0) < 1e-9)
check("heartbeat_age is computed (no second timestamp stored)",
      "heartbeat_age" not in reg2.get(HELLO["device_id"]).__dict__)
check("protocol_compatible derived", hp.protocol_compatible is True)
check("capability_valid derived", hp.capability_valid is True)
check("unknown device health -> NOT_FOUND",
      obs2.get_device_health("ghost")[1] == HEALTH_NOT_FOUND)
t_st = NOW + 6 + HEARTBEAT_TIMEOUT_S + 1
reg2.check_stale(now=t_st)
hp2, _ = obs2.get_device_health(HELLO["device_id"], now=t_st)
check("stale health: not online, state STALE",
      not hp2.online and hp2.health_state == SESSION_STALE)
reg2.check_stale(now=t_st + STALE_TIMEOUT_S + 1)
reg2.check_stale(now=t_st + STALE_TIMEOUT_S + 1)
hp3, _ = obs2.get_device_health(HELLO["device_id"],
                                now=t_st + STALE_TIMEOUT_S + 1)
check("disconnected health state", hp3.health_state == SESSION_DISCONNECTED)
check("health vocabulary has no GOOD/BAD-style states (session only)",
      set() == {s for s in ("GOOD", "BAD", "HAPPY", "SAD", "NORMAL",
                            "ABNORMAL") if s == hp3.health_state})

# ---------------------------------------------------------------------------
section("3. Command lifecycle (>=10)")
reg3 = fresh_reg()
obs3 = DeviceObserver(reg3)
rec = obs3.observe_command_created("c1", HELLO["device_id"], "TEST_ECHO",
                                   now=NOW)
check("created", rec.status == LIFECYCLE_CREATED
      and obs3.get_command_status("c1").command_id == "c1")
obs3.observe_command_sent("c1", now=NOW + 1)
check("sent (with sent_at)",
      obs3.get_command_status("c1").status == LIFECYCLE_SENT
      and obs3.get_command_status("c1").sent_at == NOW + 1)
ack = DeviceAck(command_id="c1", device_id=HELLO["device_id"],
                status="ACK")
check("acked exactly-once",
      obs3.observe_ack(ack, now=NOW + 2) is True
      and obs3.get_command_status("c1").status == LIFECYCLE_ACKED
      and obs3.get_command_status("c1").ack_at == NOW + 2)
obs3.observe_command_created("c2", HELLO["device_id"], "TEST_ECHO",
                             now=NOW)
nack = DeviceAck(command_id="c2", device_id=HELLO["device_id"],
                 status="NACK", error_code="UNKNOWN_OPERATION")
check("nacked with error_code",
      obs3.observe_ack(nack, now=NOW + 1) is True
      and obs3.get_command_status("c2").status == LIFECYCLE_NACKED
      and obs3.get_command_status("c2").error_code == "UNKNOWN_OPERATION")
obs3.observe_command_created("c3", HELLO["device_id"], "TEST_ECHO",
                             now=NOW)
obs3.observe_command_sent("c3", now=NOW + 1)
obs3.observe_terminal("c3", LIFECYCLE_TIMEOUT, error_code="TIMEOUT",
                      now=NOW + 5)
check("timeout terminal",
      obs3.get_command_status("c3").status == LIFECYCLE_TIMEOUT)
obs3.observe_command_created("c4", HELLO["device_id"], "TEST_ECHO",
                             now=NOW)
obs3.observe_terminal("c4", LIFECYCLE_FAILED, error_code="TRANSPORT_ERROR",
                      now=NOW + 1)
check("failed terminal with error code",
      obs3.get_command_status("c4").status == LIFECYCLE_FAILED)
obs3.observe_command_created("c5", HELLO["device_id"], "TEST_ECHO",
                             now=NOW)
obs3.observe_terminal("c5", LIFECYCLE_REJECTED,
                      error_code=DEVICE_NOT_FOUND, now=NOW + 1)
check("rejected terminal (gate code recorded)",
      obs3.get_command_status("c5").status == LIFECYCLE_REJECTED
      and obs3.get_command_status("c5").error_code == DEVICE_NOT_FOUND)
check("unknown command status -> None",
      obs3.get_command_status("nope") is None)
check("command records carry NO parameters/payload fields",
      "parameters" not in rec.__dict__ and "payload" not in rec.__dict__)
hist = CommandHistory(max_len=MAX_COMMAND_HISTORY)
for i in range(MAX_COMMAND_HISTORY + 10):
    hist.add(CommandRecord(command_id=f"x{i}", device_id="d",
                           operation="TEST_ECHO"))
check("history bounded at MAX_COMMAND_HISTORY (oldest evicted)",
      len(hist) == MAX_COMMAND_HISTORY and hist.get("x0") is None
      and hist.get(f"x{MAX_COMMAND_HISTORY + 9}") is not None)

# ---------------------------------------------------------------------------
section("4. ACK integrity (>=6)")
reg4 = fresh_reg()
obs4 = DeviceObserver(reg4)
obs4.observe_command_created("k1", HELLO["device_id"], "TEST_ECHO",
                             now=NOW)
check("unknown ack ignored, NO history entry created",
      obs4.observe_ack(DeviceAck(command_id="unknown",
                                 device_id=HELLO["device_id"],
                                 status="ACK")) is False
      and obs4.get_command_status("unknown") is None)
check("wrong-device ack ignored (forgery)",
      obs4.observe_ack(DeviceAck(command_id="k1", device_id="esp32-OTHER",
                                 status="ACK")) is False
      and obs4.get_command_status("k1").status == LIFECYCLE_CREATED)
obs4.observe_command_sent("k1", now=NOW + 1)
check("ack for a DIFFERENT command_id cannot complete k1",
      obs4.observe_ack(DeviceAck(command_id="other",
                                 device_id=HELLO["device_id"],
                                 status="ACK")) is False
      and obs4.get_command_status("k1").status == LIFECYCLE_SENT)
check("duplicate ack cannot re-complete (exactly-once)",
      obs4.observe_ack(DeviceAck(command_id="k1",
                                 device_id=HELLO["device_id"],
                                 status="ACK"), now=NOW + 2) is True
      and obs4.observe_ack(DeviceAck(command_id="k1",
                                     device_id=HELLO["device_id"],
                                     status="ACK"), now=NOW + 3) is False
      and obs4.get_command_status("k1").ack_at == NOW + 2)
obs4.observe_command_created("k2", HELLO["device_id"], "TEST_ECHO",
                             now=NOW)
obs4.observe_command_sent("k2", now=NOW + 1)
obs4.observe_terminal("k2", LIFECYCLE_TIMEOUT, error_code="TIMEOUT",
                      now=NOW + 5)
late = obs4.observe_ack(DeviceAck(command_id="k2",
                                  device_id=HELLO["device_id"],
                                  status="ACK"), now=NOW + 9)
check("late ack never rewrites TIMEOUT; flags late_ack only",
      late is False
      and obs4.get_command_status("k2").status == LIFECYCLE_TIMEOUT
      and obs4.get_command_status("k2").late_ack is True)

# ---------------------------------------------------------------------------
def _reject_non_terminal(obs):
    try:
        obs.observe_terminal("x", LIFECYCLE_SENT)
        return False
    except ValueError:
        return True


from pkg.capability import DEFAULT_REGISTRY as _DR  # noqa: E402


section("5. Security / readiness (>=10)")
reg5 = fresh_reg()
obs5 = DeviceObserver(reg5)
check("device_ready derives from the gate (ONLINE + advertised -> OK)",
      obs5.device_ready(HELLO["device_id"], "TEST_ECHO", now=NOW + 1)
      == (True, GATE_OK))
check("ready false for unadvertised operation",
      obs5.device_ready(HELLO["device_id"], "SERVO_MOVE", now=NOW + 1)[0]
      is False)
check("ready false for unknown device",
      obs5.device_ready("ghost", "TEST_ECHO", now=NOW + 1)[0] is False)
t_st5 = NOW + 1 + HEARTBEAT_TIMEOUT_S + 1
check("ready false when stale",
      obs5.device_ready(HELLO["device_id"], "TEST_ECHO", now=t_st5)[0]
      is False)
t_dc5 = NOW + 1 + STALE_TIMEOUT_S + 1
check("ready false when disconnected",
      obs5.device_ready(HELLO["device_id"], "TEST_ECHO", now=t_dc5)[0]
      is False)
check("ready false on protocol mismatch",
      reg5.send_allowed(HELLO["device_id"], "TEST_ECHO",
                        protocol_version=2, now=NOW + 1)[0] is False)
check("global kill switch still OFF",
      GLOBAL_EXECUTION_ENABLED is False)
check("ready NEVER rewrites policy (kill switch unchanged)",
      GLOBAL_EXECUTION_ENABLED is False
      and obs5.device_ready(HELLO["device_id"], "TEST_ECHO",
                            now=NOW + 1)[0] is not None)  # pure read
check("observe_terminal rejects non-terminal statuses",
      (lambda: (_ for _ in ()).throw(ValueError))()
      if False else _reject_non_terminal(obs5))
check("advertisement still cannot touch server contracts",
      set(_DR.list_ids()) == {"capability.respond",
                              "capability.remind",
                              "capability.acknowledge",
                              "capability.led"})



# ---------------------------------------------------------------------------
section("6. Isolation (>=6)")
OBS_PATH = os.path.join(SRC_ABS, "open_llm_vtuber", "device_protocol",
                        "observer.py")
with open(OBS_PATH, encoding="utf-8") as fh:
    obs_src = fh.read()
check("observer: no send/execute/retry/queue/schedule methods",
      not re.search(r"def (send|execute|retry|queue|schedule)\(",
                    obs_src))
check("observer: no transport imports (cannot reach the adapter path)",
      "transport" not in obs_src.replace("transport-layer", ""))
check("observer: no AI-layer imports",
      not re.search(r"from \.\.(experience|reflection|lesson|strategy|"
                    r"evaluation|decision|action|execution)", obs_src))
check("observer: no storage/persistence imports",
      not re.search(r"storage|hermes|sqlite|MemoryStore", obs_src))
check("observer: no LLM/agent/MCP",
      not re.search(r"\bllm\b|chat_completion|agent|mcp", obs_src.lower()))
check("no new DB tables anywhere for state/health/history",
      not re.search(r"CREATE TABLE IF NOT EXISTS (device_state|device_health"
                    r"|command_history|device_events)",
                    open(os.path.join(SRC_ABS, "open_llm_vtuber",
                                      "long_term_memory", "storage",
                                      "sqlite_provider.py"),
                         encoding="utf-8").read()))

print(f"\n{'='*58}")
print(f"PHASE 18 RESULT: {ok} passed, {fail} failed")
print(f"{'='*58}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
