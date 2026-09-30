"""DecisionEngine: form and persist decisions from stored evaluations.

Phase 9 orchestration: pulls EvaluationRecords through the EVALUATION
repository (never strategies directly — the chain is Evaluation ->
Decision), runs the deterministic rule analyzer (always) and optionally
the LLM refinement, persists DecisionRecords through the DECISION
repository. Pure data layer — the chain ENDS here (Decision -> STOP);
no conversation hooks, no actions, no injections.

Re-deciding over the same evaluations creates a new timestamped
record (decision history by design, same as Phase 8 evaluations).
"""

from typing import List, Optional

from loguru import logger

from ..evaluation.repository import EvaluationRepository
from ..evaluation.schemas import EvaluationRecord
from .analyzer import LLMDecisionAnalyzer, RuleDecisionAnalyzer
from .repository import DecisionRepository
from .schemas import DecisionRecord


class DecisionEngine:
    """Form explainable decisions from a conf's evaluations."""

    def __init__(self, decision_repo: DecisionRepository,
                 evaluation_repo: EvaluationRepository,
                 config: Optional[dict] = None, llm=None):
        self.decision_repo = decision_repo
        self.evaluation_repo = evaluation_repo
        self.config = config or {}
        self.rules = RuleDecisionAnalyzer()
        self.llm_analyzer = LLMDecisionAnalyzer(llm, self.config)

    # -- public API ------------------------------------------------------------

    def decide_recent(self, conf_uid: str, limit: int = 50) -> Optional[DecisionRecord]:
        """Decide over the most recent evaluations (rule layer, sync).

        Returns the persisted decision (including ABSTAIN outcomes —
        an abstain is a valid, recorded decision), or None only when
        the decision pipeline itself failed.
        """
        evaluations = self.evaluation_repo.list_evaluations(limit=limit)
        try:
            rec = self.rules.decide(evaluations, conf_uid)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[DEC] rule decision failed: {e}")
            return None
        try:
            self.decision_repo.save(rec)
            return rec
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[DEC] decision save failed: {e}")
            return None

    def decide_over(self, conf_uid: str,
                    evaluations: List[EvaluationRecord]) -> Optional[DecisionRecord]:
        """Decide over an explicit evaluation list (rule layer)."""
        try:
            rec = self.rules.decide(evaluations, conf_uid)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[DEC] rule decision failed: {e}")
            return None
        try:
            self.decision_repo.save(rec)
            return rec
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[DEC] decision save failed: {e}")
            return None

    async def decide_recent_llm(
        self, conf_uid: str, limit: int = 50
    ) -> Optional[DecisionRecord]:
        """LLM refinement over recent evaluations (offline/batch only).

        Falls back to the rule decision when the LLM is unavailable or
        its output is rejected — explicit (logged), same-domain.
        """
        evaluations = self.evaluation_repo.list_evaluations(limit=limit)
        rule_rec = None
        try:
            rule_rec = self.rules.decide(evaluations, conf_uid)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[DEC] rule decision failed: {e}")
        if not self.llm_analyzer.available() or not evaluations:
            if rule_rec is not None:
                self.decision_repo.save(rule_rec)
            return rule_rec
        try:
            rec = await self.llm_analyzer.decide(evaluations, conf_uid)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[DEC] llm decision failed: {e}")
            rec = None
        if rec is None:
            logger.info("[DEC] llm unavailable/rejected; explicit rule fallback")
            if rule_rec is not None:
                self.decision_repo.save(rule_rec)
            return rule_rec
        try:
            self.decision_repo.save(rec)
            return rec
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[DEC] llm decision rejected at save: {e}")
            return rule_rec
