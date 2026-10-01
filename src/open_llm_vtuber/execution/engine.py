"""ExecutionEngine: run intents through the sandbox and persist results.

Phase 11 orchestration — the ONLY entry into the execution layer:

    fetch ActionIntent
      -> re-validate against the STORED chain (engine never trusts the
         intent object's provenance claims): decision exists + selected,
         evaluation exists and matches, strategy exists and matches,
         and the intent's ids equal the chain's ids
      -> SandboxExecutor.execute (independent gates + strict param
         schema + deterministic simulation)
      -> ExecutionResult persisted via the execution repository
      -> STOP (return the record — nothing real is ever performed)

The engine never re-decides, re-evaluates or re-ranks anything. It
also never mutates the ActionIntent (Phase-10 intents are immutable
records); each execution ADDS an ExecutionResult.

Repeated execution of the same intent creates a NEW timestamped
result (execution history by design — documented idempotency
decision, consistent with Phases 8-10).
"""

from typing import Optional

from loguru import logger

from ..action.repository import ActionRepository
from ..action.schemas import ActionIntentRecord
from ..decision.repository import DecisionRepository
from ..decision.schemas import STATUS_SELECTED
from ..evaluation.repository import EvaluationRepository
from .repository import ExecutionRepository
from .sandbox import SandboxExecutor
from .schemas import ExecutionResult


class ExecutionEngine:
    """Execute intents in the sandbox; persist the results."""

    def __init__(self, execution_repo: ExecutionRepository,
                 action_repo: ActionRepository,
                 decision_repo: DecisionRepository,
                 evaluation_repo: EvaluationRepository,
                 config: Optional[dict] = None):
        self.execution_repo = execution_repo
        self.action_repo = action_repo
        self.decision_repo = decision_repo
        self.evaluation_repo = evaluation_repo
        self.config = config or {}
        self.sandbox = SandboxExecutor()

    # -- public API ------------------------------------------------------------

    def execute_action_sandbox(
        self, action_id: str, conf_uid: str,
    ) -> Optional[ExecutionResult]:
        """Run one intent through the sandbox. Returns the persisted result
        (SIMULATED / REJECTED / FAILED), or None only when the intent id
        itself is unknown — never a fabricated result."""
        intent = self.action_repo.get(action_id)
        if intent is None or intent.conf_uid != conf_uid:
            logger.warning(
                f"[SBX] action intent '{action_id}' not found for {conf_uid}")
            return None

        # engine-side provenance re-validation against the STORED chain
        # (the executor re-checks presence; equality is verified HERE
        # against live records so a hand-crafted intent cannot pass)
        chain_error = self._verify_chain(intent, conf_uid)
        if chain_error:
            rec = ExecutionResult.new(conf_uid, action_id, "REJECTED")
            rec.decision_id = intent.decision_id
            rec.evaluation_id = intent.evaluation_id
            rec.strategy_id = intent.strategy_id
            rec.reason = f"provenance verification failed: {chain_error}"
            try:
                self.execution_repo.save(rec)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[SBX] rejected-result save failed: {e}")
            return rec

        result = self.sandbox.execute(intent, conf_uid)
        try:
            self.execution_repo.save(result)
            return result
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[SBX] execution result save failed: {e}")
            return result

    # -- internals ----------------------------------------------------------------

    def _verify_chain(self, intent: ActionIntentRecord,
                      conf_uid: str) -> Optional[str]:
        """Verify the intent's provenance against stored records.

        Returns an error string, or None when the full chain checks out:
        decision exists + selected + its selected ids match the intent's,
        and the evaluation's strategy id matches too.
        """
        if not intent.decision_id:
            return "intent has no decision_id"
        decision = self.decision_repo.get(intent.decision_id)
        if decision is None or decision.conf_uid != conf_uid:
            return f"decision '{intent.decision_id}' not found in this conf"
        if decision.status != STATUS_SELECTED:
            return f"decision status is '{decision.status}' (not selected)"
        if decision.selected_evaluation_id != intent.evaluation_id:
            return ("intent.evaluation_id does not match "
                    "decision.selected_evaluation_id")
        if decision.selected_strategy_id != intent.strategy_id:
            return ("intent.strategy_id does not match "
                    "decision.selected_strategy_id")
        evaluation = self.evaluation_repo.get(intent.evaluation_id)
        if evaluation is None or evaluation.conf_uid != conf_uid:
            return f"evaluation '{intent.evaluation_id}' not found in this conf"
        if evaluation.strategy_id != intent.strategy_id:
            return ("evaluation.strategy_id does not match "
                    "intent.strategy_id")
        return None
