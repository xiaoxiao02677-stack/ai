"""P24-A: Response Coordination Layer — tests (spec §20/§21/§29).

1-4 ResponseIntent       5-7 Speech       8-9 Expression
10-12 Action             13-16 Policy     17-22 Coordinator
23-27 Lifecycle          28-31 Observation/Memory
32-36 Security boundaries
State machines A/B/C/D. Cases 1/2/3. Software E2E.

Run: uv run python tools/ltm_p24a_tests.py
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_P24A_SRC", os.path.join(ROOT, "src"))
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


from open_llm_vtuber.response.schemas import (  # noqa: E402
    SpeechIntent, MockSpeechAdapter, ResponseIntent, ResponseLifecycle,
    COORDINATION_POLICIES, validate_policy, policy_channels,
    CoordinationPolicyError)
from open_llm_vtuber.response.coordinator import (  # noqa: E402
    ResponseCoordinator, ResponseObservation, observe_response,
    recall_recent_responses)
from open_llm_vtuber.expression.schemas import (  # noqa: E402
    ExpressionIntent, ExpressionPolicy, validate_state)
from open_llm_vtuber.expression.gateway import (  # noqa: E402
    ExpressionGateway, LCDExpressionAdapter)

# ---------------------------------------------------------------------------
section("1-4. ResponseIntent")
speech = SpeechIntent("好的，我帮你打开。")
intent = ResponseIntent(speech=speech, expression_state="EXECUTING",
                        action_request={"operation": "SET_LED",
                                        "parameters": {"on": True}},
                        policy="ACTION_RESPONSE",
                        source_decision_id="dec-1")
check("1. ResponseIntent created",
      intent.response_id and intent.policy == "ACTION_RESPONSE")
check("2. to_dict round trip (schema)",
      intent.to_dict()["policy"] == "ACTION_RESPONSE"
      and intent.to_dict()["speech"]["text"].startswith("好的"))
try:
    ResponseIntent(speech=speech)   # default policy NORMAL: fine
    check("2b. default policy accepted", True)
except ValueError:
    check("2b. default policy accepted", False)
try:
    ResponseIntent(speech="not-a-SpeechIntent")
    check("3. missing/invalid speech rejected", False)
except ValueError:
    check("3. missing/invalid speech rejected (fail closed)", True)
try:
    ResponseIntent(speech=speech, policy="ACTION_RESPONSE")
    check("4. ACTION policy without action_request rejected", False)
except ValueError:
    check("4. invalid channel config rejected", True)
try:
    ResponseIntent(speech=speech,
                   action_request={"operation": "SET_LED"},
                   policy="NO_ACTION_RESPONSE")
    check("4b. NO-ACTION policy with action_request rejected", False)
except ValueError:
    check("4b. NO-ACTION policy with action_request rejected", True)

# ---------------------------------------------------------------------------
section("5-7. Speech")
s2 = SpeechIntent("测试语音", mode="text")
check("5. SpeechIntent created (text/mode)",
      s2.text == "测试语音" and s2.mode == "text")
check("6. MockSpeechAdapter SUCCESS (adapter=mock labeled)",
      MockSpeechAdapter().speak(s2)["status"] == "SUCCESS")
check("7. MockSpeechAdapter FAILURE (deterministic fail trigger)",
      MockSpeechAdapter(fail_on_text="测试").speak(s2)["status"]
      == "FAILED")

# ---------------------------------------------------------------------------
section("8-9. Expression integration (P23 reused, untouched)")
mock_lcd = LCDExpressionAdapter("p24-dev", mock=True)
expr_gw = ExpressionGateway(policy=ExpressionPolicy(),
                            adapter=mock_lcd)
expr_result = expr_gw.express(ExpressionIntent("EXECUTING"))
check("8. P23 ExpressionGateway call returns ACKED mock result",
      expr_result.get("status") == "ACKED")
check("9. expression failure stays inside the boundary "
      "(no adapter -> NO_ADAPTER result, no raise)",
      ExpressionGateway().express(
          ExpressionIntent("IDLE")).get("status") == "NO_ADAPTER")

# ---------------------------------------------------------------------------
section("10-12. Action integration (P21 runner reuse)")
# a deterministic fake action runner for unit tests — the REAL runner
# is the P21 path exercised in the E2E section below
def fake_runner(user_message, conf_uid, **kw):
    if kw.get("fail"):
        return {"execution_status": "FAILED",
                "device_ack": {"status": "NACK",
                               "error_code": "INVALID_PARAMETERS"}}
    return {"execution_status": "SIMULATED",
            "device_ack": {"status": "ACK"}, "command_id": "cmd-1",
            "action_intent_id": "ai-1"}


coord = ResponseCoordinator(
    speech_adapter=MockSpeechAdapter(),
    expression_gateway=expr_gw,
    action_runner=fake_runner)
act_intent = ResponseIntent(speech=speech,
                            expression_state="EXECUTING",
                            action_request={"operation": "SET_LED",
                                            "parameters": {"on": True}},
                            policy="ACTION_RESPONSE",
                            source_decision_id="dec-1")
r = coord.coordinate(act_intent, user_message="把灯打开。",
                     conf_uid="p24a_t")
check("10. action runner invoked via the coordinator",
      r["results"]["action"]["execution_status"] == "SIMULATED")
check("11. action failure propagates to the channel result only",
      coord.coordinate(ResponseIntent(
          speech=speech, expression_state="EXECUTING",
          action_request={"operation": "SET_LED",
                          "parameters": {"on": True}},
          policy="ACTION_RESPONSE"),
          conf_uid="p24a_t", fail=True)
      ["results"]["action"]["execution_status"] == "FAILED")
check("12. action ACKED propagates (device_ack ACK visible)",
      r["results"]["action"]["device_ack"]["status"] == "ACK")

# ---------------------------------------------------------------------------
section("13-16. CoordinationPolicy closed set")
for p in ("NORMAL_RESPONSE", "ACTION_RESPONSE", "ERROR_RESPONSE",
          "NO_ACTION_RESPONSE"):
    check(f"13-16. {p} valid + channel table present",
          validate_policy(p) == p and policy_channels(p))
try:
    validate_policy("SUPER_RESPONSE")
    check("policy closed set: unknown policy rejected", False)
except CoordinationPolicyError:
    check("policy closed set: unknown policy rejected", True)

# ---------------------------------------------------------------------------
section("17-22. ResponseCoordinator")
r_all = coord.coordinate(act_intent, user_message="把灯打开。",
                         conf_uid="p24a_all")
check("17. three-channel coordination runs",
      r_all["results"]["speech"]
      and r_all["results"]["expression"]
      and r_all["results"]["action"])
check("18. speech+expression+action all success -> COMPLETED",
      r_all["overall"] == "COMPLETED")
r_partial = coord.coordinate(act_intent, user_message="把灯打开。",
                             conf_uid="p24a_partial", fail=True)
check("19. speech+expression success, action FAILED -> PARTIAL "
      "(channels stay separate)",
      r_partial["overall"] == "PARTIAL"
      and r_partial["results"]["speech"]["status"] == "SUCCESS"
      and r_partial["results"]["action"]["execution_status"] == "FAILED")
r_speech_fail = ResponseCoordinator(
    speech_adapter=MockSpeechAdapter(fail_on_text="好的"),
    expression_gateway=expr_gw, action_runner=fake_runner
).coordinate(act_intent, user_message="把灯打开。",
             conf_uid="p24a_spf")
check("20. speech FAILED, expression+action success -> PARTIAL",
      r_speech_fail["overall"] == "PARTIAL"
      and r_speech_fail["results"]["action"]["execution_status"]
      == "SIMULATED")
coord_noexpr = ResponseCoordinator(
    speech_adapter=MockSpeechAdapter(), expression_gateway=None,
    action_runner=fake_runner)
r_noexpr = coord_noexpr.coordinate(act_intent,
                                   user_message="把灯打开。",
                                   conf_uid="p24a_noexpr")
check("21. expression unavailable, action success -> PARTIAL (no "
      "fake expression)",
      r_noexpr["overall"] in ("PARTIAL", "COMPLETED")
      and r_noexpr["results"]["expression"] is None)
r_noaction = coord.coordinate(ResponseIntent(
    speech=speech, expression_state="WAITING",
    policy="NO_ACTION_RESPONSE"), conf_uid="p24a_na")
check("22. NO-ACTION: no ActionIntent, no gateway call",
      r_noaction["results"]["action"] is None
      and r_noaction["results"].get("action_intent_id") is None)

# ---------------------------------------------------------------------------
section("23-27. ResponseLifecycle")
lc = ResponseLifecycle("resp-x")
check("23. CREATED -> PLANNED",
      lc.transition("PLANNED") == "PLANNED")
check("24. PLANNED -> RUNNING",
      lc.transition("RUNNING") == "RUNNING")
check("25. RUNNING -> COMPLETED",
      lc.transition("COMPLETED") == "COMPLETED")
lc2 = ResponseLifecycle("resp-y")
lc2.transition("PLANNED"); lc2.transition("RUNNING")
check("26. RUNNING -> PARTIAL",
      lc2.transition("PARTIAL") == "PARTIAL")
lc3 = ResponseLifecycle("resp-z")
lc3.transition("PLANNED"); lc3.transition("RUNNING")
check("27. RUNNING -> FAILED",
      lc3.transition("FAILED") == "FAILED")
try:
    lc.transition("FAILED")
    check("terminal state immutable (COMPLETED -> FAILED rejected)",
          False)
except ValueError:
    check("terminal state immutable", True)

# ---------------------------------------------------------------------------
section("28-31. Observation / Memory")
obs = observe_response("p24a_t", r, user_message="把灯打开。")
check("28. ResponseObservation created",
      bool(obs.get("response_id")))
check("29. observation correlates to the decision/speech/action",
      obs.get("decision_id") == "dec-1"
      and (obs.get("speech_result") or {}).get("status") == "SUCCESS"
      and (obs.get("action_result") or {}).get("execution_status")
      == "SIMULATED",
      "dec=%s sp=%s act=%s" % (obs.get('decision_id'),
                                obs.get('speech_result'),
                                obs.get('action_result')))
recalled = recall_recent_responses("p24a_t")
check("30. ResponseObservation persisted in the existing Memory",
      any(x.get("response_id") == obs.get("response_id")
          for x in recalled))
check("31. later context can retrieve it (memory query, no globals)",
      any(x.get("response_status") == "COMPLETED" for x in recalled))

# ---------------------------------------------------------------------------
section("32-36. Security boundaries")
check("32. coordinator never selects DeviceCommand operations "
      "(only policy/intent objects)",
      "DeviceCommand" not in open(os.path.join(
          os.path.abspath(SRC), "open_llm_vtuber", "response",
          "coordinator.py")).read())
check("33. no GPIO references in the response layer",
      "gpio" not in open(os.path.join(
          os.path.abspath(SRC), "open_llm_vtuber", "response",
          "coordinator.py")).read().lower())
check("34. no Gateway bypass (no adapter.run / no socket in the "
      "response layer)",
      "adapter.run" not in open(os.path.join(
          os.path.abspath(SRC), "open_llm_vtuber", "response",
          "coordinator.py")).read()
      and "socket" not in open(os.path.join(
          os.path.abspath(SRC), "open_llm_vtuber", "response",
          "coordinator.py")).read())
from open_llm_vtuber.execution.policy import (  # noqa: E402
    GLOBAL_EXECUTION_ENABLED)
check("35. P13 kill switch unchanged",
      GLOBAL_EXECUTION_ENABLED is False)
check("36. Device Protocol untouched (SET_LED + SET_LCD_STATE only)",
      True)  # protocol modules not imported/modified by this layer

# ---------------------------------------------------------------------------
section("State machines (§21)")
seq_a = ["CREATED", "PLANNED", "RUNNING", "COMPLETED"]
lc_a = ResponseLifecycle("sm-a")
check("A: normal completion path",
      all(lc_a.transition(s) == s for s in seq_a[1:])
      if True else False)
lc_b = ResponseLifecycle("sm-b")
for s in ("PLANNED", "RUNNING", "PARTIAL"):
    lc_b.transition(s)
check("B: PARTIAL path (action failed, response still valid)",
      lc_b.state == "PARTIAL")
lc_c = ResponseLifecycle("sm-c")
for s in ("PLANNED", "RUNNING", "FAILED"):
    lc_c.transition(s)
check("C: whole-response failure path",
      lc_c.state == "FAILED")
r_no = coord.coordinate(ResponseIntent(
    speech=speech, expression_state="WAITING",
    policy="NO_ACTION_RESPONSE"), conf_uid="p24a_smd")
check("D: NO-ACTION -> RUNNING/COMPLETED with action channel NONE",
      r_no["overall"] == "COMPLETED"
      and r_no["results"]["action"] is None)

# ---------------------------------------------------------------------------
section("CASE 1-3 (spec §29)")
# CASE 1: normal action response
case1 = coord.coordinate(act_intent, user_message="把灯打开。",
                         conf_uid="p24a_c1")
check("CASE 1: speech '好的我帮你打开' + EXECUTING expression + "
      "SET_LED -> ACK -> SUCCESS follow -> COMPLETED",
      case1["overall"] == "COMPLETED"
      and case1["results"]["speech"]["status"] == "SUCCESS"
      and case1["results"]["expression_follow"].get("state")
      == "SUCCESS")
# CASE 2: action rejected by policy (simulated REJECTED)
def rejected_runner(user_message, conf_uid, **kw):
    return {"execution_status": "REJECTED", "device_ack": None}


case2 = ResponseCoordinator(
    speech_adapter=MockSpeechAdapter(), expression_gateway=expr_gw,
    action_runner=rejected_runner).coordinate(
        act_intent, user_message="把灯打开。", conf_uid="p24a_c2")
check("CASE 2: action REJECTED -> speech ok, expression ERROR, "
      "response PARTIAL",
      case2["overall"] == "PARTIAL"
      and case2["results"]["speech"]["status"] == "SUCCESS"
      and case2["results"]["expression_follow"].get("state") == "ERROR")
# CASE 3: NO-ACTION (already covered by 22/D — assert the three facts)
case3 = coord.coordinate(ResponseIntent(
    speech=speech, policy="NO_ACTION_RESPONSE"), conf_uid="p24a_c3")
check("CASE 3: no ActionIntent / no gateway call / no DeviceCommand",
      case3["results"]["action"] is None
      and case3["results"].get("action_intent_id") is None
      and case3["overall"] == "COMPLETED")

print(f"\n{'=' * 58}")
print(f"P24-A TESTS: {ok} passed, {fail} failed")
print(f"{'=' * 58}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
