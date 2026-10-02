"""P22: Action -> ExecutionResult -> Observation -> Memory ->
next Context/Decision — tests.

A ACKED chain        B NACKED chain       C REJECTED
D TIMEOUT            E FAILED             F NO-ACTION
G correlation ids    H no fabricated physical claims
I memory retrieval   J second-turn context reads the observation
K second-turn decision uses it   L multi-turn no pollution
M duplicate ACK no duplicate observation   N P3-P21 regression
(suite runs separately)

Run: uv run python tools/ltm_p22_tests.py
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_P22_SRC", os.path.join(ROOT, "src"))
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


import importlib.util as _iu  # noqa: E402
_p22_path = os.path.join(HERE, "..", "src_config_p22_runtime.py")
if not os.path.exists(_p22_path):
    _p22_path = os.path.join(HERE, "..", "p22_runtime.py")
_spec = _iu.spec_from_file_location("p22_runtime", _p22_path)
p22 = _iu.module_from_spec(_spec)
_spec.loader.exec_module(p22)

import open_llm_vtuber.execution.policy as _pm  # noqa: E402
for _conf in ("p22_a", "p22_b", "p22_c", "p22_d", "p22_e", "p22_f"):
    _pm.P20_GRANT_TABLE.add(_conf)

from open_llm_vtuber.long_term_memory.store import MemoryStore  # noqa
from open_llm_vtuber.experience.repository import ExperienceRepository  # noqa
from open_llm_vtuber.execution.schemas import ExecutionResult  # noqa
from open_llm_vtuber.execution.adapter import (  # noqa: E402
    ESP32Adapter, AdapterRegistry)


class _ChannelTransport:
    def __init__(self, ack="ACK", error_code=None, device_id="p22-dev",
                 timeout=False):
        self.ack = ack
        self.error_code = error_code
        self.device_id = device_id
        self.timeout = timeout
        self.sent = []

    def send(self, mid, message, timeout=1.0):
        self.sent.append((mid, message))

    def receive(self, mid, timeout=1.0):
        if self.timeout:
            from open_llm_vtuber.device_protocol.transport import \
                TransportTimeout
            raise TransportTimeout("scripted")
        return json.dumps({
            "command_id": mid or "x", "device_id": self.device_id,
            "status": self.ack, "error_code": self.error_code,
            "protocol_version": 1, "message_type": "ack",
            "echo": {}, "created_at": time.time()})


def make_registry(transport):
    reg = AdapterRegistry()
    reg._adapters["capability.led"] = ESP32Adapter(
        "capability.led", transport, transport.device_id)
    return reg


# ---------------------------------------------------------------------------
section("A. ACKED: action -> ExecutionResult -> Observation -> Memory")
result_a = p22.handle_user_event_with_memory(
    "我今天终于把项目做完了。", conf_uid="p22_a",
    adapter_factory=lambda: make_registry(_ChannelTransport()),
    grant_switch={"global": True, "p20": True})
obs_a = result_a["observation"]
check("ACKED observation: result=success",
      obs_a is not None and obs_a["result"] == "success", str(obs_a))
check("ACKED observation persisted in memory (retrievable)",
      any(o.get("related_command_id") == result_a["run"].get("command_id")
          for o in p22.recall_recent_observations("p22_a")))

# ---------------------------------------------------------------------------
section("B. NACKED: device refusal observation")
nack_transport = _ChannelTransport(ack="NACK",
                                   error_code="INVALID_PARAMETERS")
result_b = p22.handle_user_event_with_memory(
    "我今天终于把项目做完了。", conf_uid="p22_b",
    adapter_factory=lambda: make_registry(nack_transport),
    grant_switch={"global": True, "p20": True})
obs_b = result_b["observation"]
check("NACKED observation: result=device_refused",
      obs_b["result"] == "device_refused"
      and "拒绝" in obs_b["detail"])

# ---------------------------------------------------------------------------
section("C. REJECTED: policy rejection observation")
result_c = p22.handle_user_event_with_memory(
    "我今天终于把项目做完了。", conf_uid="p22_c",
    adapter_factory=lambda: make_registry(_ChannelTransport()),
    grant_switch={"global": False, "p20": True})   # kill switch OFF
obs_c = result_c["observation"]
check("REJECTED observation: result=policy_rejected",
      obs_c["result"] == "policy_rejected"
      and "策略" in obs_c["detail"])

# ---------------------------------------------------------------------------
section("D. TIMEOUT + E. FAILED observations")
to_transport = _ChannelTransport(timeout=True)
result_d = p22.handle_user_event_with_memory(
    "我今天终于把项目做完了。", conf_uid="p22_d",
    adapter_factory=lambda: make_registry(to_transport),
    grant_switch={"global": True, "p20": True})
check("TIMEOUT observation recorded",
      result_d["observation"]["result"] in ("timeout", "failed"))
result_e = p22.handle_user_event_with_memory(
    "我今天终于把项目做完了。", conf_uid="p22_e")
check("FAILED/not-dispatched observation recorded",
      result_e["observation"]["result"]
      in ("not_dispatched", "failed"))

# ---------------------------------------------------------------------------
section("F. NO-ACTION observation")
result_f = p22.handle_user_event_with_memory("现在几点？", conf_uid="p22_f")
obs_f = result_f["observation"]
check("NO-ACTION observation: intentionally_no_action",
      obs_f["result"] == "intentionally_no_action"
      and obs_f["action"] == "none")
check("NO-ACTION is not an execution failure (distinct result)",
      obs_f["result"] != "failed")

# ---------------------------------------------------------------------------
section("G. correlation ids")
obs_a2 = result_a["observation"]
check("observation carries command_id + action_intent_id + decision_id",
      obs_a2.get("related_command_id")
      and obs_a2.get("related_action_intent_id")
      and obs_a2.get("related_decision_id"))

# ---------------------------------------------------------------------------
section("H. no fabricated physical claims")
check("ACKED wording: device confirmation only, no room-level claims",
      "ESP32 确认执行 SET_LED ON" in obs_a["detail"]
      and "房间" not in obs_a["detail"]
      and "变亮" not in obs_a["detail"])

# ---------------------------------------------------------------------------
section("I/J/K. memory retrieval -> next context -> next decision")
ctx = result_a["next_context"]
check("J. second-turn context contains the just-made observation",
      "SET_LED ON" in ctx and "最近" in ctx)
# K: a follow-up decision reads the observation from MEMORY (not from
# the previous return value) — build context for a fresh query and
# verify the observation is retrievable by the query path
ctx2 = p22.build_context("p22_a", "你刚才做了什么？")
check("K. fresh query retrieves the observation through memory",
      "SET_LED ON" in ctx2 and "来自记忆" in ctx2)

# ---------------------------------------------------------------------------
section("L. multi-turn: no cross-contamination")
check("L. per-conf isolation: p22_b has no ACKED observation",
      all(o["result"] != "success"
          for o in p22.recall_recent_observations("p22_b")))

# ---------------------------------------------------------------------------
section("M. duplicate ACK -> no duplicate observation")
before = len(p22.recall_recent_observations("p22_a"))
p22.observe_execution("p22_a", run_result=result_a["run"])
after = len(p22.recall_recent_observations("p22_a"))
check("M. repeated observation for the same command is deduped",
      before == after)

print(f"\n{'=' * 58}")
print(f"P22 TESTS: {ok} passed, {fail} failed")
print(f"{'=' * 58}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
