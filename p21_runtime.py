"""P21 runtime: real user event -> AI decision chain -> real body.

The MINIMAL orchestration layer the P21 spec asked for — every domain
object below is an EXISTING P4-P20 class, reused untouched:

  handle_user_event(user_message)
    -> ExperienceEngine.start (P4 ExperienceRecord)
    -> RuleAnalyzer reflection (P5) -> RuleAnalyzer lessons (P6)
    -> RuleAnalyzer strategies (P7) -> RuleAnalyzer evaluation (P8)
    -> Decision (P9, status selected/abstain; metadata
       source='deterministic_fixture' — never disguised as an LLM)
    -> [when the decision selects a body action]
       ActionIntent (P10) -> Resolver (P12) -> decide_p20 (P13/P20)
       -> ExecutionGateway (P13/P20) -> ESP32Adapter (P15) with the
       REAL gateway-channel transport (P19-R/P20 wiring)
    -> P21RunResult (correlation ids + execution outcome; reuses the
       P18 lifecycle semantics — no second status model)

NO-ACTION IS A FIRST-CLASS OUTCOME (spec §16): a decision may select
answer-only — no ActionIntent, no device command, transport untouched.
"""
import time
from typing import Any, Dict, List, Optional

try:
    from src.open_llm_vtuber.experience.engine import ExperienceEngine
    from src.open_llm_vtuber.experience.repository import \
        ExperienceRepository
    from src.open_llm_vtuber.reflection.analyzer import \
        RuleAnalyzer as ReflectionRules
    from src.open_llm_vtuber.reflection.repository import \
        ReflectionRepository
    from src.open_llm_vtuber.lesson.analyzer import \
        RuleAnalyzer as LessonRules
    from src.open_llm_vtuber.lesson.repository import LessonRepository
    from src.open_llm_vtuber.strategy.analyzer import \
        RuleAnalyzer as StrategyRules
    from src.open_llm_vtuber.strategy.schemas import StrategyRecord
    from src.open_llm_vtuber.strategy.repository import StrategyRepository
    from src.open_llm_vtuber.evaluation.analyzer import \
        RuleAnalyzer as EvaluationRules
    from src.open_llm_vtuber.evaluation.repository import \
        EvaluationRepository
    from src.open_llm_vtuber.decision.schemas import (
        DecisionRecord, STATUS_SELECTED as DEC_SELECTED,
        STATUS_ABSTAIN as DEC_ABSTAIN)
    from src.open_llm_vtuber.decision.repository import DecisionRepository
    from src.open_llm_vtuber.action.schemas import (
        ActionIntentRecord, STATUS_PLANNED)
    from src.open_llm_vtuber.action.repository import ActionRepository
    from src.open_llm_vtuber.long_term_memory.store import MemoryStore
except ImportError:
    from open_llm_vtuber.experience.engine import ExperienceEngine  # type: ignore
    from open_llm_vtuber.experience.repository import (  # type: ignore
        ExperienceRepository)
    from open_llm_vtuber.reflection.analyzer import (  # type: ignore
        RuleAnalyzer as ReflectionRules)
    from open_llm_vtuber.reflection.repository import (  # type: ignore
        ReflectionRepository)
    from open_llm_vtuber.lesson.analyzer import (  # type: ignore
        RuleAnalyzer as LessonRules)
    from open_llm_vtuber.lesson.repository import LessonRepository  # type: ignore
    from open_llm_vtuber.strategy.analyzer import (  # type: ignore
        RuleAnalyzer as StrategyRules)
    from open_llm_vtuber.strategy.schemas import StrategyRecord  # type: ignore
    from open_llm_vtuber.strategy.repository import StrategyRepository  # type: ignore
    from open_llm_vtuber.evaluation.analyzer import (  # type: ignore
        RuleAnalyzer as EvaluationRules)
    from open_llm_vtuber.evaluation.repository import (  # type: ignore
        EvaluationRepository)
    from open_llm_vtuber.decision.schemas import (  # type: ignore
        DecisionRecord, STATUS_SELECTED as DEC_SELECTED,
        STATUS_ABSTAIN as DEC_ABSTAIN)
    from open_llm_vtuber.decision.repository import DecisionRepository  # type: ignore
    from open_llm_vtuber.action.schemas import (  # type: ignore
        ActionIntentRecord, STATUS_PLANNED)
    from open_llm_vtuber.action.repository import ActionRepository  # type: ignore
    from open_llm_vtuber.long_term_memory.store import MemoryStore  # type: ignore

# positive-milestone vocabulary (P21 fixture scenario §8): the
# deterministic rules read the SAME Experience text the reflection
# analyzers see — no hidden if/led shortcut anywhere.
_POSITIVE_MARKERS = ("终于", "做完", "完成", "搞定", "达成", "毕业",
                     "上线", "通过", "成功")


class P21RunResult(dict):
    """Correlation + outcome view of one P21 run (P18/P20 statuses)."""


def _is_positive_milestone(user_message: str) -> bool:
    text = user_message or ""
    return any(m in text for m in _POSITIVE_MARKERS)


def handle_user_event(
    user_message: str,
    conf_uid: str = "p21",
    *,
    adapter_factory=None,
    policy=None,
    grant_switch: Optional[Dict[str, bool]] = None,
) -> P21RunResult:
    """One real user event through the FULL existing chain.

    adapter_factory: callable(transport) -> ESP32Adapter-bound registry;
    when None (or when it returns None) the run is DECISION-ONLY — the
    body is not touched (used by the no-device unit tests; the REAL
    path passes the gateway-channel wiring from the workshop runtime).
    grant_switch: dev-only switch simulation for tests (production
    leaves the code constants untouched).
    """
    # 1. Experience (P4) — the REAL schema, sanitized by the engine
    engine = ExperienceEngine.start(conf_uid, interaction_type="chat")
    engine.record_user_input(user_message)
    engine.record_ai_response("")   # chat reply happens elsewhere; P21
    #                                    only drives the BODY decision
    engine.record_outcome("用户报告事件")
    exp = engine.finalize()
    store = MemoryStore(conf_uid)
    exp_repo = ExperienceRepository(store.provider)
    exp_repo.add(exp)

    # 2. Reflection (P5 rules) over the just-recorded experience
    reflection = ReflectionRules().analyze([exp], conf_uid,
                                           reflection_type="outcome_distribution")
    ReflectionRepository(store.provider).save(reflection)

    # 3. Lessons (P6 rules) — support-based distillation
    lessons = LessonRules(min_support=1).analyze([reflection], conf_uid)
    lesson_repo = LessonRepository(store.provider)
    for lesson in lessons:
        lesson_repo.save(lesson)

    # 4. Strategies (P7 rules) — cluster lessons into advisory strategy
    strategies = StrategyRules(min_lessons=1).analyze(lessons, conf_uid)
    strategy_repo = StrategyRepository(store.provider)
    for strategy in strategies:
        strategy_repo.save(strategy)

    # P21 deterministic fixture strategy (clearly labeled): the rules
    # pipeline produces statistical strategies whose conditions rarely
    # match a single message; the fixture adds the milestone-conditioned
    # body strategy the P21 scenario needs. It is a REAL StrategyRecord
    # and still has to pass the REAL rule evaluation below.
    fixture_lesson_ids = [l.lesson_id for l in lessons] or ["p21-fixture"]
    fixture_strategy = StrategyRecord.new(conf_uid, fixture_lesson_ids)
    fixture_strategy.condition = "用户 终于 做完 项目"
    fixture_strategy.recommendation = "点亮设备 LED 表示庆祝"
    fixture_strategy.evidence = list(fixture_lesson_ids)
    fixture_strategy.confidence = 0.8
    fixture_strategy.metadata = {"source": "deterministic_fixture",
                                 "p21": True}
    strategy_repo.save(fixture_strategy)
    strategies = list(strategies) + [fixture_strategy]

    # 5. Evaluation (P8 rules) — context = the user's own message
    evaluator = EvaluationRules()
    decision_repo = DecisionRepository(store.provider)
    action_repo = ActionRepository(store.provider)

    result = P21RunResult({
        "experience_id": exp.experience_id,
        "user_message": user_message,
        "no_action": True,
        "decision_id": None,
        "action_intent_id": None,
        "command_id": None,
        "device_id": None,
        "session_id": None,
        "execution_status": None,
        "device_ack": None,
    })

    # 6. Decision (P9) — deterministic fixture, clearly labeled
    positive = _is_positive_milestone(user_message)
    decision = DecisionRecord.new(conf_uid, DEC_ABSTAIN)
    decision.reason = ("P21 deterministic decision fixture: "
                       + ("积极里程碑事件" if positive else "普通对话（无需身体动作）"))
    decision.metadata = {"source": "deterministic_fixture",
                         "p21_positive_milestone": positive}

    chosen_strategy = None
    chosen_evaluation = None
    if positive and strategies:
        # evaluate the strategy against the SAME user message context
        best = None
        for strategy in strategies:
            evaluation = evaluator.evaluate(strategy, user_message)
            EvaluationRepository(store.provider).save(evaluation)
            if evaluation.applicable and (best is None
                                          or evaluation.relevance
                                          > best[1].relevance):
                best = (strategy, evaluation)
        if best is not None:
            chosen_strategy, chosen_evaluation = best
            decision.status = DEC_SELECTED
            decision.selected_strategy_id = chosen_strategy.strategy_id
            decision.selected_evaluation_id = \
                chosen_evaluation.evaluation_id
            decision.confidence = chosen_evaluation.relevance
            decision.reason += "；选择庆祝身体动作"
    decision_repo.save(decision)
    result["decision_id"] = decision.decision_id

    # no-action path (§16): ABSTAIN or nothing selected -> answer only
    if chosen_strategy is None:
        result["no_action"] = True
        return result

    # 7. ActionIntent (P10) — the body action the decision selected
    intent = ActionIntentRecord.new(conf_uid, decision.decision_id,
                                    "SET_LED")
    intent.evaluation_id = chosen_evaluation.evaluation_id
    intent.strategy_id = chosen_strategy.strategy_id
    intent.parameters = {"on": True}   # celebrate: light the LED
    intent.reason = "P21 积极里程碑庆祝动作（fixture 决策链产物）"
    intent.status = STATUS_PLANNED
    action_repo.save(intent)
    result["action_intent_id"] = intent.action_id
    result["no_action"] = False

    # 8. Execution (P12->P13/P20) — ONLY when a real adapter factory is
    # provided; otherwise the run records the intent and stops BEFORE
    # the gateway (unit tests / device-offline semantics).
    if adapter_factory is None:
        result["execution_status"] = "NOT_DISPATCHED"
        return result

    from open_llm_vtuber.execution.gateway import ExecutionGateway
    from open_llm_vtuber.execution.repository import ExecutionRepository
    from open_llm_vtuber.execution.policy import ExecutionPolicy
    import open_llm_vtuber.execution.policy as policy_mod

    registry = adapter_factory()
    if registry is None:
        result["execution_status"] = "NOT_DISPATCHED"
        return result

    policy_obj = policy or ExecutionPolicy()
    switches = None
    if grant_switch is not None:
        switches = (policy_mod.GLOBAL_EXECUTION_ENABLED,
                    policy_mod.P20_SWITCH_ON)
        policy_mod.GLOBAL_EXECUTION_ENABLED = grant_switch.get(
            "global", False)
        policy_mod.P20_SWITCH_ON = grant_switch.get("p20", False)
    try:
        gw = ExecutionGateway(
            ExecutionRepository(store.provider),
            action_repo,
            decision_repo,
            EvaluationRepository(store.provider),
            adapter_registry=registry,
            policy=policy_obj)
        exec_result = gw.execute(intent.action_id, conf_uid)
    finally:
        if switches is not None:
            (policy_mod.GLOBAL_EXECUTION_ENABLED,
             policy_mod.P20_SWITCH_ON) = switches

    if exec_result is None:
        result["execution_status"] = "NO_RESULT"
        return result
    result["execution_status"] = exec_result.status
    result["command_id"] = exec_result.result.get("command_id")
    result["device_id"] = exec_result.result.get("device_id")
    result["device_ack"] = (exec_result.result.get("device_ack")
                            if isinstance(exec_result.result, dict)
                            else None)
    return result
