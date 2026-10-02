"""P22 runtime: Action -> ExecutionResult -> Observation -> Memory ->
next Context/Decision.

The closed feedback loop after P21: the system records what IT just
did, stores that as retrievable memory, and surfaces it in the next
turn's context.

Design (minimal-invasive, per the P22 audit):

- Observation is NOT a new object system. It is a structured record of
  "what the system observed about its own action", carried through the
  EXISTING P4 Experience channel: an experience row with
  metadata.p22_observation = {subject, action, target, result, detail,
  related_command_id, related_action_intent_id, ...}. Persisted by the
  existing ExperienceRepository into the existing experiences table —
  no second memory system, no new tables, no provider bypass.

- observe_execution(...) builds the Observation from a REAL
  ExecutionResult / P21RunResult and saves it. Wording NEVER claims
  physical-world outcomes beyond the device's own confirmation
  ("ESP32 确认执行 SET_LED ON" — never "房间变亮").

- recall_recent_observations(conf_uid) retrieves them back through the
  REAL repository query (no globals, no temp dicts).

- build_context(conf_uid, user_message) composes the next-turn context
  including the recent observations — the retrieval path the next
  Decision/Response reads.

- NO-ACTION is a first-class observation (intentionally_no_action),
  produced from the P21 abstain branch WITHOUT touching sealed P21
  code (the runtime wraps handle_user_event's result).
"""
import json
import time
from typing import Any, Dict, List, Optional

try:
    from src.open_llm_vtuber.experience.engine import ExperienceEngine
    from src.open_llm_vtuber.experience.repository import \
        ExperienceRepository
    from src.open_llm_vtuber.long_term_memory.store import MemoryStore
except ImportError:
    from open_llm_vtuber.experience.engine import ExperienceEngine  # type: ignore
    from open_llm_vtuber.experience.repository import (  # type: ignore
        ExperienceRepository)
    from open_llm_vtuber.long_term_memory.store import MemoryStore  # type: ignore

# P21 runtime: next to this file in production, or alongside the test
# harness in dev layouts — search both, then the CWD.
import importlib.util as _iu
import os as _os
_here = _os.path.dirname(_os.path.abspath(__file__))


def _load_p21():
    for cand in (_os.path.join(_here, "p21_runtime.py"),
                 _os.path.join(_here, "..", "p21_runtime.py"),
                 _os.path.join(_os.getcwd(), "p21_runtime.py")):
        if _os.path.isfile(cand):
            spec = _iu.spec_from_file_location("p21_runtime", cand)
            mod = _iu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise ImportError("p21_runtime.py not found")


p21 = _load_p21()

# execution-status -> observation result mapping (closed set)
_RESULT_MAP = {
    "EXECUTED": "success",
    "SIMULATED": "sandbox_success",
    "ACKED": "success",
    "NACKED": "device_refused",
    "REJECTED": "policy_rejected",
    "TIMEOUT": "timeout",
    "FAILED": "failed",
    "NOT_DISPATCHED": "not_dispatched",
    "NO_RESULT": "failed",
}

# physical-claim wording per operation: ONLY what the device itself
# confirms — never real-world side effects ("房间变亮" is forbidden)
_OP_DETAIL = {
    "SET_LED": {"target": "LED", "on": "ESP32 确认执行 SET_LED ON。",
                "off": "ESP32 确认执行 SET_LED OFF。",
                "other": "ESP32 确认执行 SET_LED。"},
}


def _detail_for(status_result: str, operation: str,
                parameters: Dict[str, Any], device_ack: Optional[dict]) -> str:
    """Honest wording: device-confirmed facts only."""
    if status_result == "intentionally_no_action":
        return "决策为不行动：本轮无设备命令。"
    if status_result == "success" and device_ack:
        on = parameters.get("on")
        table = _OP_DETAIL.get(operation, {})
        if operation == "SET_LED" and isinstance(on, bool):
            return table.get("on" if on else "off", table.get("other", ""))
        return table.get("other", f"设备确认执行 {operation}。")
    if status_result == "device_refused":
        code = (device_ack or {}).get("error_code") or "NACK"
        return f"设备拒绝执行该命令（{code}）。"
    if status_result == "policy_rejected":
        return "执行被策略拒绝（未到达设备）。"
    if status_result == "timeout":
        return "设备在超时时间内未确认。"
    if status_result == "failed":
        return "执行失败（详见执行记录）。"
    if status_result == "sandbox_success":
        return "沙箱模拟执行完成（无真实设备动作）。"
    if status_result == "not_dispatched":
        return "动作意图已记录但未派发到设备。"
    return "执行结束。"


def observe_execution(
    conf_uid: str,
    *,
    run_result: Optional[Dict[str, Any]] = None,
    execution_result=None,
    user_message: str = "",
) -> Optional[Dict[str, Any]]:
    """Record an Observation of a completed action into Memory.

    Accepts either a P21RunResult dict (user-event path, incl.
    no-action) or a raw P13 ExecutionResult (direct gateway path).
    Returns the observation payload (also embedded in the stored
    experience metadata). Dedup: a repeated ACK for the SAME
    command_id does not create a second observation.
    """
    # -- normalize inputs ------------------------------------------------
    if run_result is not None:
        status = run_result.get("execution_status")
        operation = "SET_LED"
        parameters = {"on": True}
        command_id = run_result.get("command_id")
        action_intent_id = run_result.get("action_intent_id")
        decision_id = run_result.get("decision_id")
        device_ack = run_result.get("device_ack")
        no_action = bool(run_result.get("no_action"))
    elif execution_result is not None:
        payload = getattr(execution_result, "result", None) or {}
        status = getattr(execution_result, "status", None)
        operation = payload.get("operation", "SET_LED")
        parameters = payload.get("parameters", {})
        command_id = payload.get("command_id")
        action_intent_id = getattr(execution_result, "action_id", None)
        decision_id = getattr(execution_result, "decision_id", None)
        device_ack = (True if payload.get("device_ack") is True else None)
        no_action = False
    else:
        return None

    # dedup: same command already observed -> no second observation
    if command_id:
        existing = recall_recent_observations(conf_uid, limit=50)
        for obs in existing:
            if obs.get("related_command_id") == command_id:
                return obs  # idempotent: reuse the first observation

    if no_action:
        result = "intentionally_no_action"
    else:
        result = _RESULT_MAP.get(status or "", "failed")
        # device-level refusal: the gateway folds a NACK into FAILED,
        # but the observation layer can see the NACK in device_ack —
        # classify it as the device refusing, not a generic failure
        if (result == "failed" and isinstance(device_ack, dict)
                and device_ack.get("status") == "NACK"):
            result = "device_refused"
        # a REAL device ACK is the device's own confirmation of
        # execution (workshop path: gateway SANDBOX verdict + real
        # channel ACK) — classify as device-confirmed success
        if (result in ("failed", "sandbox_success")
                and isinstance(device_ack, dict)
                and device_ack.get("status") == "ACK"):
            result = "success"

    observation = {
        "subject": "self",
        "action": "none" if no_action else operation,
        "target": _OP_DETAIL.get(operation, {}).get("target", "device"),
        "result": result,
        "detail": _detail_for(result, operation, parameters, device_ack),
        "source": "execution",
        "timestamp": time.time(),
        "related_command_id": command_id,
        "related_action_intent_id": action_intent_id,
        "related_decision_id": decision_id,
        "execution_status": status,
        "user_message": (user_message or "")[:200],
    }

    # -- persist through the REAL Experience channel (P4) ------------------
    engine = ExperienceEngine.start(conf_uid, interaction_type="proactive")
    engine.record_user_input("")
    engine.record_ai_response("")
    engine.record_outcome(f"[P22 Observation] {observation['detail']}")
    exp = engine.finalize(outcome_type="turn_complete")
    # structured payload rides in metadata — real schema, no new table
    meta = dict(exp.metadata or {})
    meta["p22_observation"] = observation
    exp.metadata = meta
    ExperienceRepository(MemoryStore(conf_uid).provider).add(exp)
    return observation


def recall_recent_observations(conf_uid: str,
                               limit: int = 10) -> List[Dict[str, Any]]:
    """Retrieve recent observations through the REAL repository query.

    No globals: every call reads the persisted experiences.
    """
    repo = ExperienceRepository(MemoryStore(conf_uid).provider)
    out: List[Dict[str, Any]] = []
    for exp in repo.list_recent(limit=200):
        obs = (exp.metadata or {}).get("p22_observation")
        if isinstance(obs, dict):
            out.append(obs)
        if len(out) >= limit:
            break
    return out


def build_context(conf_uid: str, user_message: str = "",
                  max_observations: int = 5) -> str:
    """Compose the next-turn context including recent observations.

    This is the retrieval path the next Decision/Response reads: the
    observations come from Memory (persisted experiences), never from
    a global or the previous turn's return value.
    """
    parts: List[str] = []
    if user_message:
        parts.append(f"用户当前消息：{user_message}")
    obs_list = recall_recent_observations(conf_uid,
                                          limit=max_observations)
    if obs_list:
        lines = []
        for obs in reversed(obs_list):   # oldest -> newest
            t = time.strftime("%H:%M:%S",
                              time.localtime(obs.get("timestamp", 0)))
            lines.append(f"- [{t}] {obs.get('detail', '')}"
                         f"（结果：{obs.get('result', '')}）")
        parts.append("系统最近的自身动作记录（来自记忆）：\n" + "\n".join(lines))
    return "\n".join(parts)


def handle_user_event_with_memory(user_message: str,
                                  conf_uid: str = "p22",
                                  **kwargs) -> Dict[str, Any]:
    """P22 closed loop: P21 run + Observation + memory-backed context.

    The P21 sealed runtime is called UNTOUCHED; this wrapper only adds
    the observation recording and the next-context retrieval.
    """
    run = p21.handle_user_event(user_message, conf_uid=conf_uid, **kwargs)
    observation = observe_execution(conf_uid, run_result=run,
                                    user_message=user_message)
    context = build_context(conf_uid, user_message)
    return {
        "run": run,
        "observation": observation,
        "next_context": context,
    }
