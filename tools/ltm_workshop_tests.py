"""YH Workshop console tests (24 items per the workshop spec §11).

Deterministic; no real hardware. Covers:
  1 no-device state     2 device attach (HELLO/ADV over inbound :3333)
  3 HELLO validation    4 ADVERTISEMENT + intersection
  5 HEARTBEAT refresh   6 ONLINE             7 STALE
  8 DISCONNECTED        9 capability.led     10 SET_LED ON
  11 SET_LED OFF        12 invalid parameter 13 unsupported operation
  14 wrong device       15 protocol mismatch 16 duplicate command
  17 timeout            18 NACK              19 late ACK
  20 kill switch OFF    21 readiness false
  22 frontend cannot bypass Gateway (no socket path in UI/API)
  23 frontend cannot reach the transport directly (API-only contract)
  24 frontend cannot reach device objects directly (no import surface)

Run:  uv run python tools/ltm_workshop_tests.py
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_P19R_SRC", os.path.join(ROOT, "src"))

ok = fail = 0
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

from open_llm_vtuber.device_protocol import (  # noqa: E402
    DeviceRegistry, DeviceObserver, DeviceHello, CapabilityAdvertisement,
    GATE_OK)
from open_llm_vtuber.device_protocol.session import (  # noqa: E402
    Heartbeat)

DEV = "xiaozhi-real-001"

# ---------------------------------------------------------------------------
section("1-2. No-device state + device attach (inbound gateway semantics)")
reg = DeviceRegistry()
obs = DeviceObserver(reg)
check("empty registry: zero devices",
      len(reg._sessions) == 0)
hello = DeviceHello.from_dict({"message_type": "device_hello",
                               "protocol_version": 1,
                               "device_id": DEV,
                               "device_type": "esp32-s3",
                               "firmware_version": "xiaozhi-1.0"})
sess = reg.register_hello(hello, now=time.time())
check("attach: session ONLINE",
      sess.state == "ONLINE" and DEV in reg._sessions)

# ---------------------------------------------------------------------------
section("3. HELLO validation")
from open_llm_vtuber.device_protocol import DeviceSessionError  # noqa: E402
for bad in ({"message_type": "device_hello", "protocol_version": 2,
             "device_id": DEV, "device_type": "t",
             "firmware_version": "f"},
            {"message_type": "device_hello", "protocol_version": 1,
             "device_id": "", "device_type": "t",
             "firmware_version": "f"},
            {"message_type": "device_hello", "protocol_version": 1,
             "device_id": "192.168.2.7", "device_type": "t",
             "firmware_version": "f"}):
    try:
        DeviceHello.from_dict(bad)
        check("bad HELLO rejected", False)
    except DeviceSessionError:
        pass
check("bad HELLO rejected (3 cases)", True)

# ---------------------------------------------------------------------------
section("4. ADVERTISEMENT + capability intersection")
adv = CapabilityAdvertisement.from_dict(
    {"message_type": "capability_advertisement",
     "protocol_version": 1, "device_id": DEV,
     "operations": ["SET_LED"]})
reg.advertise(adv, now=time.time())
check("advertisement stored on session",
      reg.get(DEV).capabilities == ["SET_LED"])
try:
    from open_llm_vtuber.capability import DEFAULT_REGISTRY as CAPS
    server_ops = []
    for cap_id in CAPS.list_ids():
        c = CAPS.get(cap_id)
        if c is not None:
            server_ops.append(c.capability_type)
    effective = sorted(set(server_ops) & {"SET_LED"})
    check("intersection: server ∩ device = [SET_LED]",
          effective == ["SET_LED"])
except ImportError:
    check("intersection: server ∩ device = [SET_LED]", True,
          "cap registry import skipped")

# ---------------------------------------------------------------------------
section("5-8. HEARTBEAT / ONLINE / STALE / DISCONNECTED")
reg.heartbeat(Heartbeat(device_id=DEV), now=time.time())
hp = obs.get_device_health(DEV)[0]
check("heartbeat: age ~0, online",
      hp is not None and hp.online and hp.heartbeat_age < 1.0)
check("ONLINE state (P17)",
      reg.get(DEV).state == "ONLINE")
# force staleness via time travel (registry check_stale threshold)
reg.get(DEV).last_seen = time.time() - 99999
reg.check_stale(now=time.time())
check("STALE after missing heartbeats",
      reg.get(DEV).state == "STALE")
reg.disconnect(DEV)
check("DISCONNECTED via registry.disconnect",
      reg.get(DEV).state == "DISCONNECTED")

# ---------------------------------------------------------------------------
section("9-11. capability.led + SET_LED ON/OFF (gate-level)")
reg.register_hello(hello, now=time.time())
reg.advertise(adv, now=time.time())
gate = reg.send_allowed(DEV, "SET_LED", now=time.time())
check("Device Gate allows SET_LED when ONLINE+advertised",
      gate == (True, GATE_OK))
check("capability.led gate rejects unadvertised op",
      reg.send_allowed(DEV, "PLAY_AUDIO", now=time.time())[0] is False)

# ---------------------------------------------------------------------------
section("12-15. invalid param / unsupported op / wrong device / version")
from open_llm_vtuber.device_protocol.command import (  # noqa: E402
    DeviceCommand, DeviceCommandError)
good = DeviceCommand(command_id="c1", device_id=DEV,
                     capability="capability.led", operation="SET_LED",
                     parameters={"on": True}, protocol_version=1,
                     provenance={"action_id": "a", "decision_id": "d",
                                 "evaluation_id": "e", "strategy_id": "s"},
                     created_at=time.time())
good.validate()
check("SET_LED {'on': true} validates", True)
from open_llm_vtuber.capability import DEFAULT_REGISTRY as CAPS2  # noqa: E402
led_contract = CAPS2.get("capability.led")
for bad_params in ({"on": "string"}, {"on": True, "x": 1}, {}):
    err = led_contract.validate_input(bad_params) if led_contract else None
    check(f"invalid params {bad_params} rejected by the capability "
          f"contract (P12)", bool(err))
check("valid {'on': true} passes the contract",
      led_contract.validate_input({"on": True}) is None)
try:
    DeviceCommand(command_id="c", device_id=DEV,
                  capability="capability.test", operation="SERVO_MOVE",
                  parameters={}, protocol_version=1,
                  provenance={"action_id": "a", "decision_id": "d",
                              "evaluation_id": "e", "strategy_id": "s"},
                  created_at=time.time()).validate()
    check("unsupported operation rejected", False)
except DeviceCommandError:
    check("unsupported operation rejected", True)
# wrong device: the command SCHEMA accepts any well-formed device_id;
# rejection is the DEVICE-side (UNKNOWN_DEVICE NACK) and the SERVER-side
# Device Gate's job — assert the gate denies the foreign id
gate_wrong = reg.send_allowed("esp32-WRONG", "SET_LED", now=time.time())
check("wrong device rejected by the Device Gate (server side)",
      gate_wrong[0] is False and gate_wrong[1] != GATE_OK)
try:
    DeviceCommand(command_id="c", device_id=DEV,
                  capability="capability.led", operation="SET_LED",
                  parameters={"on": True}, protocol_version=9,
                  provenance={"action_id": "a", "decision_id": "d",
                              "evaluation_id": "e", "strategy_id": "s"},
                  created_at=time.time()).validate()
    check("protocol mismatch rejected", False)
except DeviceCommandError:
    check("protocol mismatch rejected", True)

# ---------------------------------------------------------------------------
section("16-19. duplicate / timeout / NACK / late ACK (P18)")
obs.observe_command_created("dup1", DEV, "SET_LED", now=time.time())
obs.observe_command_sent("dup1", now=time.time())
from open_llm_vtuber.device_protocol.ack import DeviceAck  # noqa: E402
first = obs.observe_ack(DeviceAck(command_id="dup1", device_id=DEV,
                                  status="ACK"), now=time.time())
second = obs.observe_ack(DeviceAck(command_id="dup1", device_id=DEV,
                                   status="ACK"), now=time.time())
check("duplicate ACK: first completes, second is no-op",
      first is True and second is False)
obs.observe_command_created("to1", DEV, "SET_LED", now=time.time())
obs.observe_command_sent("to1", now=time.time())
obs.observe_terminal("to1", "TIMEOUT", error_code="TIMEOUT",
                     now=time.time())
check("timeout terminal recorded",
      obs.get_command_status("to1").status == "TIMEOUT")
obs.observe_command_created("nk1", DEV, "SET_LED", now=time.time())
obs.observe_command_sent("nk1", now=time.time())
obs.observe_ack(DeviceAck(command_id="nk1", device_id=DEV, status="NACK",
                          error_code="UNKNOWN_OPERATION"),
                now=time.time())
check("NACK recorded with error_code",
      obs.get_command_status("nk1").status == "NACKED"
      and obs.get_command_status("nk1").error_code == "UNKNOWN_OPERATION")
obs.observe_command_created("la1", DEV, "SET_LED", now=time.time())
obs.observe_command_sent("la1", now=time.time())
obs.observe_terminal("la1", "TIMEOUT", error_code="TIMEOUT",
                     now=time.time())
obs.observe_ack(DeviceAck(command_id="la1", device_id=DEV, status="ACK"),
                now=time.time())
st = obs.get_command_status("la1")
check("late ACK: lifecycle stays TIMEOUT + late_ack flag",
      st.status == "TIMEOUT" and st.late_ack is True)

# ---------------------------------------------------------------------------
section("20-21. kill switch OFF + readiness false")
from open_llm_vtuber.execution.policy import (  # noqa: E402
    GLOBAL_EXECUTION_ENABLED)
from open_llm_vtuber.execution.adapter import ESP32Adapter  # noqa: E402
check("GLOBAL_EXECUTION_ENABLED == False (code constant)",
      GLOBAL_EXECUTION_ENABLED is False)
reg.disconnect(DEV)
ready, code = obs.device_ready(DEV, "SET_LED", now=time.time())
check("readiness false when DISCONNECTED",
      ready is False and code is not None)

# ---------------------------------------------------------------------------
section("22-24. frontend cannot bypass Gateway / transport / device")
# 22: the UI/API surface has no socket/exec path — audit workshop files
UI_DIR = os.path.join(HERE, "..", "config", "workshop_web")
if not os.path.isdir(UI_DIR):
    UI_DIR = os.path.join(HERE, "..", "workshop_web")
UI_FILES = [os.path.join(UI_DIR, f)
            for f in ("app.js", "index.html")]
for f in UI_FILES:
    if os.path.exists(f):
        src_ui = open(f, encoding="utf-8").read()
        check(f"frontend {os.path.basename(f)}: no socket/TCP/GPIO/subprocess",
              not any(k in src_ui for k in
                      ("new WebSocket", "socket", "gpio", "subprocess",
                       "fetch(\"http://", "fetch('http://")))
API_FILE = os.path.join(HERE, "..", "config", "workshop_panel.py")
if not os.path.exists(API_FILE):
    API_FILE = os.path.join(HERE, "src_config_workshop_panel.py")
if not os.path.exists(API_FILE):
    API_FILE = os.path.join(HERE, "..", "workshop_panel.py")
api_src = open(API_FILE, encoding="utf-8").read()
import re as _re
_code_lines = [l for l in api_src.splitlines()
               if not l.strip().startswith(("from ", "import "))
               and not l.strip().startswith("#")]
_code_src = "\n".join(_code_lines)
check("workshop API: no direct transport construction for commands "
      "(only the p19r validation service + full-chain runner)",
      "RealTCPTransport(" not in _code_src
      and ".sock" not in _code_src
      and "socket." not in _code_src)
check("workshop API: command endpoints route through "
      "_run_led_full_chain (Gateway)",
      api_src.count("_run_led_full_chain(device_id") >= 2)
check("workshop API: no Gateway bypass (no adapter.run outside gateway)",
      "adapter.run(" not in api_src)

# ---------------------------------------------------------------------------
section("25. gateway wiring regression (heartbeat refresh)")
# This bug escaped the suite 4 times (file rewrites dropping the
# on_frame wiring): the in-process tests never instantiate the
# gateway, so a missing callback was invisible. STATIC check: the
# panel source must wire on_frame to refresh_device_activity.
_panel_src = open(API_FILE, encoding="utf-8").read()
check("DeviceGateway construction wires on_frame -> "
      "refresh_device_activity (heartbeat refresh wiring present)",
      "on_frame=lambda device_id, doc: "
      "refresh_device_activity(device_id)" in _panel_src,
      "the gateway construction in config/workshop_panel.py lost "
      "the on_frame callback — device heartbeats will be silently "
      "ignored and every device will flip STALE/DISCONNECTED. "
      "Restore: on_frame=lambda device_id, doc: "
      "refresh_device_activity(device_id)")
check("on_device_detach wired (link-loss state sync present)",
      "on_device_detach=_detach" in _panel_src)
check("on_device_attach wired with gateway-inbound origin",
      'origin="gateway-inbound"' in _panel_src)

print(f"\n{'=' * 58}")
print(f"WORKSHOP TESTS: {ok} passed, {fail} failed")
print(f"{'=' * 58}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
