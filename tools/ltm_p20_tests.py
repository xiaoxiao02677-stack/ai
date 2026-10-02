"""P20: AI -> real body first closed loop — test suite (spec §15/§16).

Chain under test (EVERY step through the REAL objects):
  P20-01 Decision -> ActionIntent
  P20-02 ActionIntent -> CapabilityResolver
  P20-03 Resolver -> Policy (grant table + kill switch)
  P20-04 Policy -> Gateway
  P20-05 Gateway -> ESP32Adapter (real channel transport)
  P20-06 full chain Decision -> DeviceCommand (deterministic ids)
  P20-07 REAL device: Decision -> LED ON/OFF -> Device ACK -> ACKED
Negative (§16): unknown capability / unsupported operation / device
offline / kill switch / invalid parameters.

Run: uv run python tools/ltm_p20_tests.py [--real]
  --real requires LTM_P20_DEVICE (device_id) and a live gateway link.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_P20_SRC", os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.abspath(SRC))

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


# ---------------------------------------------------------------------------
section("P20-01 Decision -> ActionIntent (real objects)")
from open_llm_vtuber.decision.schemas import DecisionRecord, \
    STATUS_SELECTED as DEC_SELECTED   # noqa: E402
from open_llm_vtuber.action.schemas import ActionIntentRecord, \
    STATUS_PLANNED   # noqa: E402
from open_llm_vtuber.evaluation.schemas import EvaluationRecord  # noqa: E402
from open_llm_vtuber.strategy.schemas import StrategyRecord  # noqa: E402
from open_llm_vtuber.long_term_memory.store import MemoryStore  # noqa: E402
from open_llm_vtuber.decision.repository import DecisionRepository  # noqa
from open_llm_vtuber.action.repository import ActionRepository  # noqa
from open_llm_vtuber.evaluation.repository import EvaluationRepository  # noqa
from open_llm_vtuber.strategy.repository import StrategyRepository  # noqa
from open_llm_vtuber.execution.repository import ExecutionRepository  # noqa
from open_llm_vtuber.execution.gateway import ExecutionGateway  # noqa
from open_llm_vtuber.execution.policy import (  # noqa: E402
    ExecutionPolicy, GLOBAL_EXECUTION_ENABLED, POLICY_DENY)
import open_llm_vtuber.execution.policy as _pm  # noqa: E402


def build_chain(conf_uid, on, strategy_repo=None, evaluation_repo=None,
                decision_repo=None, action_repo=None):
    """P20-DEV deterministic Decision fixture (clearly labeled, never
    disguised as an LLM decision): full stored provenance chain ending
    in a SET_LED ActionIntent."""
    store = MemoryStore(conf_uid)
    srepo = strategy_repo or StrategyRepository(store.provider)
    erepo = evaluation_repo or EvaluationRepository(store.provider)
    drepo = decision_repo or DecisionRepository(store.provider)
    arepo = action_repo or ActionRepository(store.provider)

    strategy = StrategyRecord.new(conf_uid, ["p20-fixture"])
    strategy.condition = "P20 设备控制意图"
    strategy.recommendation = "点亮设备 LED" if on else "熄灭设备 LED"
    strategy.evidence = ["p20-fixture"]
    strategy.confidence = 0.9
    srepo.save(strategy)

    evaluation = EvaluationRecord.new(conf_uid, strategy.strategy_id)
    evaluation.applicable = True
    evaluation.relevance = 0.9
    evaluation.confidence = 0.85
    evaluation.condition_match = 0.7
    evaluation.reason = "P20 fixture"
    erepo.save(evaluation)

    decision = DecisionRecord.new(conf_uid, DEC_SELECTED)
    decision.selected_evaluation_id = evaluation.evaluation_id
    decision.selected_strategy_id = strategy.strategy_id
    decision.confidence = 0.8
    decision.reason = "P20-DEV deterministic Decision (fixture)"
    drepo.save(decision)

    intent = ActionIntentRecord.new(conf_uid, decision.decision_id,
                                    "SET_LED")
    intent.evaluation_id = evaluation.evaluation_id
    intent.strategy_id = strategy.strategy_id
    intent.parameters = {"on": on}
    intent.reason = "P20 AI->body fixture"
    intent.status = STATUS_PLANNED
    arepo.save(intent)
    return dict(store=store, strategy=strategy, evaluation=evaluation,
                decision=decision, intent=intent)


chain = build_chain("p20_auto", True)
check("P20-01 Decision (selected) created via real DecisionRecord",
      chain["decision"].status == DEC_SELECTED
      and chain["decision"].selected_strategy_id
      == chain["strategy"].strategy_id)
check("P20-01 ActionIntent (SET_LED, planned) created",
      chain["intent"].action_type == "SET_LED"
      and chain["intent"].status == STATUS_PLANNED
      and chain["intent"].parameters == {"on": True})

# ---------------------------------------------------------------------------
section("P20-02 ActionIntent -> CapabilityResolver")
from open_llm_vtuber.capability import DEFAULT_REGISTRY as CAPS, \
    get_resolver  # noqa: E402
resolution = get_resolver().resolve("SET_LED")
check("resolver resolves SET_LED -> capability.led",
      resolution.status == "RESOLVED"
      and resolution.capability.capability_id == "capability.led")
bad_res = get_resolver().resolve("NOT_A_CAPABILITY")
check("unknown action_type -> resolver reject",
      bad_res.status != "RESOLVED")

# ---------------------------------------------------------------------------
section("P20-03 Resolver -> Policy (grant table + kill switch)")
from open_llm_vtuber.execution.policy import P20_GRANT_TABLE, \
    is_granted  # noqa: E402
led_contract = CAPS.get("capability.led")
from open_llm_vtuber.execution.adapter import ESP32Adapter  # noqa: E402


class _ChannelTransport:
    """Transport-interface view over a scripted device channel (dev
    tests); the REAL run uses the gateway channel transport."""

    def __init__(self, ack_status="ACK", error_code=None,
                 device_id="p20-dev-001"):
        self.ack_status = ack_status
        self.error_code = error_code
        self.device_id = device_id
        self.sent = []

    def send(self, message_id, message, timeout=1.0):
        self.sent.append((message_id, message))

    def receive(self, message_id, timeout=1.0):
        # real DeviceAck envelope, P15 closed schema; NACK for
        # INVALID_PARAMETERS requires a non-empty command_id
        return json.dumps({
            "command_id": message_id or "p20-nack-cmd",
            "device_id": self.device_id,
            "status": self.ack_status,
            "error_code": self.error_code,
            "protocol_version": 1,
            "message_type": "ack",
            "echo": {},
            "created_at": time.time(),
        })


dev_adapter = ESP32Adapter("capability.led", _ChannelTransport("ACK"),
                           "p20-dev-001")
check("P20 grant table: not granted by default (empty -> deny)",
      is_granted("p20_nobody", dev_adapter) is False)
P20_GRANT_TABLE.add("p20_auto")
_pm.P20_SWITCH_ON = True
check("P20 grant table: granted conf passes (switch ON)",
      is_granted("p20_auto", dev_adapter) is True)
_pm.P20_SWITCH_ON = False
check("P20 grant table: switch OFF -> deny even when granted",
      is_granted("p20_auto", dev_adapter) is False)
check("P20 grant table does NOT bypass the kill switch (OFF -> DENY)",
      GLOBAL_EXECUTION_ENABLED is False
      and ExecutionPolicy().decide(led_contract, dev_adapter).status
      == POLICY_DENY)
from open_llm_vtuber.execution.policy import (  # noqa: E402
    POLICY_REAL_ALLOWED)
# dev-only simulation of the code-level switches being ON (both are
# constants in production; the test patches the module attrs in-process)
_old_g, _old_s = _pm.GLOBAL_EXECUTION_ENABLED, _pm.P20_SWITCH_ON
_pm.GLOBAL_EXECUTION_ENABLED, _pm.P20_SWITCH_ON = True, True
policy = ExecutionPolicy()
check("switches ON + grant: REAL_ALLOWED via decide_p20 (ONLY real path)",
      policy.decide_p20(led_contract, dev_adapter,
                        "p20_auto").status == POLICY_REAL_ALLOWED)
check("switches ON but conf NOT granted: DENY",
      policy.decide_p20(led_contract, dev_adapter,
                        "p20_stranger").status == POLICY_DENY)
_pm.GLOBAL_EXECUTION_ENABLED, _pm.P20_SWITCH_ON = _old_g, _old_s
check("P20 switch defaults OFF in code",
      _pm.P20_SWITCH_ON is False)

# ---------------------------------------------------------------------------
section("P20-04/05/06 Gateway -> ESP32Adapter -> DeviceCommand (full)")
# compose the gateway with a p20-enabled policy view: switch simulated ON
# via the module flag the policy reads (default OFF -> everything DENY)
policy_mod = _pm


from open_llm_vtuber.execution.adapter import AdapterRegistry  # noqa


def run_full_chain(conf_uid, on, transport, device_id,
                   policy_switch=False, registry_override=None):
    ch = build_chain(conf_uid, on)
    old = (policy_mod.GLOBAL_EXECUTION_ENABLED, policy_mod.P20_SWITCH_ON)
    if policy_switch:
        # dev-only: simulate both code-level switches ON (requires
        # GLOBAL_EXECUTION_ENABLED=True in-process)
        policy_mod.GLOBAL_EXECUTION_ENABLED = True
        policy_mod.P20_SWITCH_ON = True
    try:
        store = ch["store"]
        adapters = registry_override
        if adapters is None:
            adapters = AdapterRegistry()
            adapters._adapters["capability.led"] = ESP32Adapter(
                "capability.led", transport, device_id)
        gw = ExecutionGateway(
            ExecutionRepository(store.provider),
            ActionRepository(store.provider),
            DecisionRepository(store.provider),
            EvaluationRepository(store.provider),
            adapter_registry=adapters,
            policy=ExecutionPolicy())
        result = gw.execute(ch["intent"].action_id, conf_uid)
    finally:
        (policy_mod.GLOBAL_EXECUTION_ENABLED,
         policy_mod.P20_SWITCH_ON) = old
    return result, ch


# kill switch OFF (the live default): the SAME decision is DENIED
result_off, _ = run_full_chain("p20_off", True, _ChannelTransport("ACK"),
                               "p20-dev-001", policy_switch=False)
check("kill switch OFF: Decision -> Policy DENY (never reaches device)",
      result_off is not None and result_off.status == "REJECTED"
      and ("KILL SWITCH" in (result_off.reason or "")
           or "kill switch" in (result_off.reason or "")
           or "p20 switch" in (result_off.reason or "")),
      getattr(result_off, "reason", ""))

# switch ON + grant + scripted channel: full chain EXECUTED with a real
# DeviceAck envelope
P20_GRANT_TABLE.add("p20_on")
transport = _ChannelTransport("ACK")
result_on, ch_on = run_full_chain("p20_on", True, transport, "p20-dev-001",
                                  policy_switch=True)
check("P20-04 Gateway executed the DEVICE adapter (status EXECUTED)",
      result_on is not None and result_on.status == "EXECUTED",
      getattr(result_on, "reason", ""))
sent_msg = transport.sent[-1][1] if transport.sent else ""
check("P20-05 adapter produced a real DeviceCommand frame (YH protocol)",
      '"operation": "SET_LED"' in sent_msg
      and '"on": true' in sent_msg)
cmd_id_in_payload = (result_on is not None
                     and result_on.result.get("command_id"))
check("P20-06 ids correlated: decision->intent->command->ack",
      result_on is not None
      and result_on.decision_id == ch_on["decision"].decision_id
      and result_on.action_id == ch_on["intent"].action_id
      and bool(cmd_id_in_payload),
      f"decision={getattr(result_on, 'decision_id', None)} "
      f"action={getattr(result_on, 'action_id', None)} "
      f"cmd={cmd_id_in_payload}")

# NACK path through the full chain
P20_GRANT_TABLE.add("p20_nack")
transport_nack = _ChannelTransport("NACK", "INVALID_PARAMETERS",
                                   device_id="p20-dev-001")
result_nack, _ = run_full_chain("p20_nack", True, transport_nack,
                                "p20-dev-001", policy_switch=True)
check("device NACK -> gateway FAILED (honest, never fabricated)",
      result_nack is not None and result_nack.status == "FAILED"
      and "NACK" in (result_nack.reason or ""))

# ---------------------------------------------------------------------------
section("P20 Negative tests (§16)")
# unsupported operation: action_type not in the enum -> rejected at
# intent construction
bad_intent = ActionIntentRecord.new("p20_neg", "d", "FLY")
check("unsupported action_type sanitized to the closed enum (FLY->"
      "ACKNOWLEDGE, never reaches an adapter)",
      bad_intent.action_type == "ACKNOWLEDGE")
res_fly = get_resolver().resolve("FLY")
check("resolver rejects unknown action_type (FLY unresolved)",
      res_fly.status != "RESOLVED")
# invalid parameters: contract layer
check("on='hello' rejected by capability contract",
      bool(led_contract.validate_input({"on": "hello"})))
# device offline: session gate blocks before transport
from open_llm_vtuber.device_protocol import DeviceRegistry, DeviceHello  # noqa
reg = DeviceRegistry()
gate_adapter = ESP32Adapter("capability.led", _ChannelTransport("ACK"),
                            "ghost-device", session_registry=reg)
# (device never registered -> send_allowed denies; adapter returns
# DEVICE_GATE_REJECTED without touching the transport)
from open_llm_vtuber.execution.schemas import ExecutionResult  # noqa
request_like = None
gate_code = gate_adapter._session_gate("SET_LED")
check("device offline -> session gate blocks (code)",
      gate_code is not None)

# ---------------------------------------------------------------------------
section("P20-07 REAL device (optional --real)")
parser = argparse.ArgumentParser()
parser.add_argument("--real", action="store_true")
parser.add_argument("--device", default=os.environ.get("LTM_P20_DEVICE"))
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=12395)
args, _ = parser.parse_known_args()

if args.real and args.device:
    base = f"http://{args.host}:{args.port}/workshop/api"

    def api(path, payload=None):
        url = base + path
        if payload is None:
            with urllib.request.urlopen(url, timeout=10) as r:
                return json.loads(r.read())
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    overview = api("/overview")
    dev = next((d for d in overview["devices"]
                if d["device_id"] == args.device), None)
    check("REAL: device present and ONLINE",
          dev is not None and dev["connection_state"] == "ONLINE")
    caps = api(f"/devices/{args.device}/capabilities")
    check("REAL: SET_LED in the effective intersection",
          "SET_LED" in caps["effective_operations"])

    # REAL P20 loop: Decision fixture -> full chain -> device ACK.
    led_on = api(f"/devices/{args.device}/led", {"on": True})
    check("REAL: LED ON via the full chain — device ACK",
          led_on.get("device_ack", {}).get("status") == "ACK",
          json.dumps(led_on)[:120])
    cmds = api(f"/devices/{args.device}/commands")
    acked = [c for c in cmds["commands"]
             if c["status"] == "ACKED" and c["operation"] == "SET_LED"]
    check("REAL: lifecycle ACKED recorded (command history)",
          bool(acked))
    led_off = api(f"/devices/{args.device}/led", {"on": False})
    check("REAL: LED OFF via the full chain — device ACK",
          led_off.get("device_ack", {}).get("status") == "ACK",
          json.dumps(led_off)[:120])
    print("\n  >>> [HUMAN] 请观察真实 ESP32 的 WS2812：应已熄灭。"
          "确认亮/灭过程是否符合（回报 PASS/FAIL）。")
else:
    print("  (--real not set — device phase skipped; run with --real "
          "--device <id> against the live server)")

print(f"\n{'=' * 58}")
print(f"P20 TESTS: {ok} passed, {fail} failed")
print(f"{'=' * 58}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
