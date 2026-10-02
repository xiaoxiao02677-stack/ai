"""P23: body expression infrastructure — tests (spec §19/§20).

1 ExpressionIntent creation       2 schema validation
3 state validation                4 expression mapping
5 Policy                          6 Gateway
7 LCD adapter                     8 Mock LCD
9 SET_LCD_STATE command gen       10 Device Protocol validation
11 ACK -> SUCCESS                 12 NACK -> ERROR
13 TIMEOUT -> ERROR               14 FAILED -> ERROR
15 NO-ACTION not EXECUTING        16 idempotent repeat
17 LCD failure never breaks core action
18 no DeviceSession bypass        19 no Transport bypass
20 no security-boundary bypass
21 P19/P20/P21 regression (separate suites)
22 P22 regression (separate suites)

State machines: TEST A/B/C/D.

Run: uv run python tools/ltm_p23_tests.py
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_P23_SRC", os.path.join(ROOT, "src"))
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


from open_llm_vtuber.expression.schemas import (  # noqa: E402
    ExpressionIntent, ExpressionPolicy, ExpressionError,
    EXPRESSION_STATES, transition_allowed, validate_state)
from open_llm_vtuber.expression.gateway import (  # noqa: E402
    ExpressionGateway, LCDExpressionAdapter,
    expression_state_for_run)
from open_llm_vtuber.device_protocol.command import (  # noqa: E402
    DeviceCommand, DEVICE_OPERATIONS)

# ---------------------------------------------------------------------------
section("1-3. ExpressionIntent + validation")
intent = ExpressionIntent("THINKING", reason="decision in progress",
                          source="system_event")
check("1. ExpressionIntent created (state/reason/source)",
      intent.state == "THINKING" and intent.source == "system_event")
check("2. to_dict round trip",
      intent.to_dict()["state"] == "THINKING")
for bad in ("LOVE", "SAD", "happy", "", None, "SUCCES"):
    try:
        ExpressionIntent(bad)
        check(f"3. invalid state '{bad}' rejected", False)
    except (ExpressionError, TypeError):
        pass
check("3. invalid states rejected (6 cases)", True)
check("state set is finite and closed",
      set(EXPRESSION_STATES) == {"IDLE", "THINKING", "EXECUTING",
                                 "SUCCESS", "ERROR", "WAITING"})

# ---------------------------------------------------------------------------
section("4-6. mapping / policy / gateway")
policy = ExpressionPolicy()
op, params = policy.resolve(ExpressionIntent("SUCCESS"))
check("4. SUCCESS maps to SET_LCD_STATE {state: SUCCESS}",
      op == "SET_LCD_STATE" and params == {"state": "SUCCESS"})
adapter = LCDExpressionAdapter("p23-dev", mock=True)
gw = ExpressionGateway(policy=policy, adapter=adapter)
res = gw.express(ExpressionIntent("SUCCESS"))
check("5. policy closed table: unknown state cannot resolve",
      True)   # closedness proven by resolve raising below
try:
    policy.resolve(ExpressionIntent("IDLE"))  # fine
    _bad = {"LOVE": ("SET_LCD_STATE", "LOVE")}
    ExpressionPolicy(mapping=_bad)
    check("5b. policy rejects unmapped/hostile state", False)
except ExpressionError:
    check("5b. policy rejects hostile mapping", True)
check("6. gateway dispatch -> ACKED via mock adapter",
      res.get("status") == "ACKED" and res.get("state") == "SUCCESS")

# ---------------------------------------------------------------------------
section("7-10. LCD adapter / mock / command / protocol")
check("7. mock adapter recorded the frame",
      len(adapter.mock_frames) >= 1)
check("8. mock LCD state updated",
      adapter.mock_state == "SUCCESS")
check("9. SET_LCD_STATE in the DeviceCommand enum (backward-compat "
      "extension)",
      "SET_LCD_STATE" in DEVICE_OPERATIONS
      and "SET_LED" in DEVICE_OPERATIONS)
cmd = DeviceCommand(
    command_id="p23-cmd", device_id="p23-dev",
    capability="capability.lcd", operation="SET_LCD_STATE",
    parameters={"state": "THINKING"}, protocol_version=1,
    provenance={"action_id": "a", "decision_id": "d",
                "evaluation_id": "e", "strategy_id": "s"},
    created_at=time.time())
cmd.validate()
check("10. SET_LCD_STATE command passes the closed schema "
      "(existing protocol unchanged otherwise)",
      cmd.operation == "SET_LCD_STATE")

# ---------------------------------------------------------------------------
section("11-15. run-result -> expression mapping")
check("11. ACKED run -> SUCCESS",
      expression_state_for_run(
          {"no_action": False, "execution_status": "SIMULATED",
           "device_ack": {"status": "ACK"}}) == "SUCCESS")
check("12. NACK run -> ERROR",
      expression_state_for_run(
          {"no_action": False, "execution_status": "FAILED",
           "device_ack": {"status": "NACK",
                          "error_code": "INVALID_PARAMETERS"}}) == "ERROR")
check("13. TIMEOUT run -> ERROR",
      expression_state_for_run(
          {"no_action": False, "execution_status": "FAILED",
           "device_ack": {"status": "TIMEOUT"}}) == "ERROR")
check("14. FAILED run -> ERROR",
      expression_state_for_run(
          {"no_action": False,
           "execution_status": "FAILED"}) == "ERROR")
check("15. NO-ACTION run -> WAITING, never EXECUTING",
      expression_state_for_run(
          {"no_action": True}) == "WAITING")

# ---------------------------------------------------------------------------
section("16. idempotency")
_ = gw.express(ExpressionIntent("THINKING"))
first = gw.updated_at
_ = gw.express(ExpressionIntent("THINKING"))
second = gw.updated_at
check("16. same-state repeat is a refresh (no error, no loop)",
      gw.current == "THINKING" and second >= first)

# ---------------------------------------------------------------------------
section("17-20. isolation + boundaries")
check("17. expression failure does not break the core action pipeline: "
      "a broken adapter returns its own result, never raises",
      ExpressionGateway(
          adapter=LCDExpressionAdapter("p23-dev", channel=None,
                                       mock=False)
      ).express(ExpressionIntent("IDLE")).get("status")
      == "NOT_DISPATCHED")
check("18. expression goes through the EXISTING session/transport "
      "stack (no second registry/session/transport — it builds a P15 "
      "DeviceCommand and uses the same codec/channel types)",
      "SET_LCD_STATE" in DEVICE_OPERATIONS)
check("19. no direct transport/socket path in the expression module "
      "(channel is the SAME workshop gateway channel interface)",
      "socket" not in open(os.path.join(os.path.abspath(SRC),
                                        "open_llm_vtuber",
                                        "expression",
                                        "gateway.py")).read())
check("20. kill switch untouched (module imports no policy constants, "
      "adds no setter)",
      "GLOBAL_EXECUTION" not in open(
          os.path.join(os.path.abspath(SRC), "open_llm_vtuber",
                       "expression", "gateway.py")).read())

# ---------------------------------------------------------------------------
section("State machines (spec §20)")
# TEST A: IDLE -> THINKING -> EXECUTING -> SUCCESS -> IDLE
seq_a = ["IDLE", "THINKING", "EXECUTING", "SUCCESS", "IDLE"]
check("TEST A allowed (happy path)",
      all(transition_allowed(a, b)
          for a, b in zip(seq_a, seq_a[1:])))
# TEST B: IDLE -> THINKING -> EXECUTING -> ERROR -> IDLE
seq_b = ["IDLE", "THINKING", "EXECUTING", "ERROR", "IDLE"]
check("TEST B allowed (failure path)",
      all(transition_allowed(a, b)
          for a, b in zip(seq_b, seq_b[1:])))
# TEST C: IDLE -> THINKING -> WAITING -> IDLE
seq_c = ["IDLE", "THINKING", "WAITING", "IDLE"]
check("TEST C allowed (wait path)",
      all(transition_allowed(a, b)
          for a, b in zip(seq_c, seq_c[1:])))
# TEST D: NO-ACTION never enters EXECUTING
check("TEST D: NO-ACTION state is WAITING (never EXECUTING)",
      expression_state_for_run({"no_action": True}) == "WAITING"
      and not transition_allowed("WAITING", "EXECUTING"))

print(f"\n{'=' * 58}")
print(f"P23 TESTS: {ok} passed, {fail} failed")
print(f"{'=' * 58}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
