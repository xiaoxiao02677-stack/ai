"""ActionEngine: create intent records from selected decisions.

Phase 10 orchestration — the ONLY entry into the action layer:

    fetch Decision
      -> status must be selected (abstain/rejected -> explicit None,
         never a planned intent)
      -> fetch the selected Evaluation
      -> verify evaluation_id / strategy_id provenance consistency
      -> shape the intent (rule layer; optional LLM refinement)
      -> schema validation (enum + parameter blacklist + provenance)
      -> persist via the action repository
      -> STOP (return the record — nothing executes it)

The engine never re-ranks, re-evaluates or re-decides: the decision
is the single source of choice. All provenance ids are derived from
the decision record, never from an LLM.
"""

from typing import Optional

from loguru import logger

from ..decision.repository import DecisionRepository
from ..decision.schemas import DecisionRecord, STATUS_SELECTED
from ..evaluation.repository import EvaluationRepository
from ..strategy.repository import StrategyRepository
from .analyzer import LLMActionAnalyzer, RuleActionAnalyzer
from .repository import ActionRepository
from .schemas import ActionIntentRecord


class ActionEngine:
    """Create side-effect-free intents from selected decisions."""

    def __init__(self, action_repo: ActionRepository,
                 decision_repo: DecisionRepository,
                 evaluation_repo: EvaluationRepository,
                 strategy_repo: StrategyRepository,
                 config: Optional[dict] = None, llm=None):
        self.action_repo = action_repo
        self.decision_repo = decision_repo
        self.evaluation_repo = evaluation_repo
        self.strategy_repo = strategy_repo
        self.config = config or {}
        self.rules = RuleActionAnalyzer()
        self.llm_analyzer = LLMActionAnalyzer(llm, self.config)

    # -- public API ------------------------------------------------------------

    def create_action_from_decision(
        self, decision_id: str, conf_uid: str,
    ) -> Optional[ActionIntentRecord]:
        """Rule-layer intent creation. Returns the persisted intent, or
        None when the decision is missing / not selected / provenance
        broken — never a fabricated planned intent."""
        decision = self.decision_repo.get(decision_id)
        if decision is None or decision.conf_uid != conf_uid:
            logger.warning(
                f"[ACT] decision '{decision_id}' not found for {conf_uid}")
            return None
        if decision.status != STATUS_SELECTED:
            logger.info(
                f"[ACT] decision {decision_id} status={decision.status}; "
                f"no planned intent (abstain/rejected barred)")
            return None

        evaluation = self.evaluation_repo.get(decision.selected_evaluation_id)
        if evaluation is None or evaluation.conf_uid != conf_uid:
            logger.warning(
                f"[ACT] evaluation '{decision.selected_evaluation_id}' "
                f"missing for {conf_uid}; provenance broken")
            return None
        if evaluation.strategy_id != decision.selected_strategy_id:
            logger.warning(
                f"[ACT] provenance mismatch: decision.strategy="
                f"{decision.selected_strategy_id} != evaluation.strategy="
                f"{evaluation.strategy_id}")
            return None

        strategy = self.strategy_repo.get(decision.selected_strategy_id)
        if strategy is None or strategy.conf_uid != conf_uid:
            logger.warning(
                f"[ACT] strategy '{decision.selected_strategy_id}' missing; "
                f"provenance broken")
            return None

        try:
            rec = self.rules.analyze(decision, strategy)
            rec.evaluation_id = decision.selected_evaluation_id
            rec.strategy_id = decision.selected_strategy_id
            self.action_repo.save(rec)   # validates + persists
            return rec
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[ACT] intent creation failed: {e}")
            return None

    async def create_action_from_decision_llm(
        self, decision_id: str, conf_uid: str,
    ) -> Optional[ActionIntentRecord]:
        """LLM-shaped variant. Falls back to the rule layer when the LLM
        is unavailable or rejected — explicit (logged), same-domain."""
        decision = self.decision_repo.get(decision_id)
        if decision is None or decision.conf_uid != conf_uid \
                or decision.status != STATUS_SELECTED:
            return self.create_action_from_decision(decision_id, conf_uid)
        evaluation = self.evaluation_repo.get(decision.selected_evaluation_id)
        if evaluation is None or evaluation.conf_uid != conf_uid \
                or evaluation.strategy_id != decision.selected_strategy_id:
            return self.create_action_from_decision(decision_id, conf_uid)
        strategy = self.strategy_repo.get(decision.selected_strategy_id)
        if strategy is None:
            return self.create_action_from_decision(decision_id, conf_uid)

        if not self.llm_analyzer.available():
            return self.create_action_from_decision(decision_id, conf_uid)
        try:
            rec = await self.llm_analyzer.analyze(decision, strategy)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[ACT] llm intent failed: {e}")
            rec = None
        if rec is None:
            logger.info("[ACT] llm unavailable/rejected; explicit rule fallback")
            return self.create_action_from_decision(decision_id, conf_uid)
        try:
            self.action_repo.save(rec)
            return rec
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[ACT] llm intent rejected at save: {e}")
            return self.create_action_from_decision(decision_id, conf_uid)
