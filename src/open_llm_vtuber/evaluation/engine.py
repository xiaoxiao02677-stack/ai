"""EvaluationEngine: judge strategy applicability for a given context.

Phase 8 orchestration: pulls StrategyRecords through the STRATEGY
repository, judges each against the current context (rule layer
always; LLM refinement optional), persists EvaluationRecords through
the EVALUATION repository. Pure data interface for future phases —
nothing in the conversation path calls this, and nothing downstream
executes based on the results.

Error discipline: empty strategy list / empty context = explicit
no-op with INFO log; a missing strategy id is skipped with a warning
(never a fabricated evaluation); analyzer exceptions log and skip.
Re-evaluating the same (strategy, context) pair creates a NEW record
by design — evaluations are timestamped history, not state (documented
idempotency decision, see the acceptance report).
"""

from typing import List, Optional

from loguru import logger

from ..strategy.repository import StrategyRepository
from ..strategy.schemas import StrategyRecord
from .analyzer import LLMAnalyzer, RuleAnalyzer
from .repository import EvaluationRepository
from .schemas import EvaluationRecord


class EvaluationEngine:
    """Judge strategies against a context; persist the judgments."""

    def __init__(self, evaluation_repo: EvaluationRepository,
                 strategy_repo: StrategyRepository,
                 config: Optional[dict] = None, llm=None):
        self.evaluation_repo = evaluation_repo
        self.strategy_repo = strategy_repo
        self.config = config or {}
        self.rules = RuleAnalyzer()
        self.llm_analyzer = LLMAnalyzer(llm, self.config)

    # -- public API ------------------------------------------------------------

    def evaluate_strategy(
        self,
        strategy_id: str,
        context: str,
        conf_uid: str,
    ) -> Optional[EvaluationRecord]:
        """Judge ONE strategy against the context (rule layer, sync).

        Returns the persisted evaluation, or None when the strategy id
        is unknown in this conf (explicit skip, never fabricated).
        """
        strategy = self.strategy_repo.get(strategy_id)
        if strategy is None or strategy.conf_uid != conf_uid:
            logger.warning(
                f"[EVL] strategy '{strategy_id}' not found for {conf_uid}; "
                f"skipping evaluation")
            return None
        try:
            rec = self.rules.evaluate(strategy, context or "")
            self.evaluation_repo.save(rec)
            return rec
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[EVL] evaluation failed for {strategy_id}: {e}")
            return None

    def evaluate_candidates(
        self,
        context: str,
        conf_uid: str,
        limit: int = 50,
    ) -> List[EvaluationRecord]:
        """Judge recent candidate strategies, ranked by relevance (desc).

        Non-applicable ones are kept in the result (explicit rejection
        is information too) but sorted below applicable ones.
        """
        if not context or not context.strip():
            logger.info("[EVL] empty context; nothing to evaluate")
            return []
        strategies = self.strategy_repo.list_strategies(limit=limit)
        if not strategies:
            logger.info(f"[EVL] no candidate strategies for {conf_uid}")
            return []
        out: List[EvaluationRecord] = []
        for st in strategies:
            try:
                rec = self.rules.evaluate(st, context)
                self.evaluation_repo.save(rec)
                out.append(rec)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[EVL] evaluation failed for "
                               f"{st.strategy_id}: {e}")
        out.sort(key=lambda r: (not r.applicable, -r.relevance))
        return out

    async def evaluate_strategy_llm(
        self,
        strategy_id: str,
        context: str,
        conf_uid: str,
    ) -> Optional[EvaluationRecord]:
        """LLM refinement for ONE strategy (offline/batch only).

        Falls back to the rule layer when the LLM is unavailable or
        rejects its output — the fallback is explicit (logged) and
        same-domain, never a silent algorithm swap.
        """
        strategy = self.strategy_repo.get(strategy_id)
        if strategy is None or strategy.conf_uid != conf_uid:
            logger.warning(
                f"[EVL] strategy '{strategy_id}' not found for {conf_uid}")
            return None
        if not self.llm_analyzer.available():
            return self.evaluate_strategy(strategy_id, context, conf_uid)
        try:
            rec = await self.llm_analyzer.evaluate(strategy, context or "")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[EVL] llm evaluation failed: {e}")
            rec = None
        if rec is None:
            logger.info("[EVL] llm unavailable/rejected; explicit rule fallback")
            return self.evaluate_strategy(strategy_id, context, conf_uid)
        try:
            self.evaluation_repo.save(rec)
            return rec
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[EVL] llm evaluation rejected at save: {e}")
            return None
