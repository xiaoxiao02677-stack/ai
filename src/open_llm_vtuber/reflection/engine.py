"""ReflectionEngine: batch analysis over persisted experiences.

Phase 5 orchestration: pulls ExperienceRecords through the EXPERIENCE
repository (never the raw provider), runs the rule analyzer (always) and
optionally the LLM analyzer (when attached + enabled + llm_analysis on),
persists resulting ReflectionRecords through the REFLECTION repository.

Strictly offline/batch: nothing in the conversation path calls this.
Empty input is a no-op with an INFO log (no error, no record). Analysis
failures log and skip — they never propagate to callers.
"""

from typing import List, Optional

from loguru import logger

from ..experience.repository import ExperienceRepository
from .analyzer import LLMAnalyzer, RuleAnalyzer
from .repository import ReflectionRepository
from .schemas import REFLECTION_TYPES, ReflectionRecord

_DEFAULT_KINDS = ("interaction_pattern", "frequency_analysis",
                  "outcome_distribution", "tool_usage")


class ReflectionEngine:
    """Derive and persist observations from a conf's experiences."""

    def __init__(self, reflection_repo: ReflectionRepository,
                 experience_repo: ExperienceRepository,
                 config: Optional[dict] = None, llm=None):
        self.reflection_repo = reflection_repo
        self.experience_repo = experience_repo
        self.config = config or {}
        self.rules = RuleAnalyzer()
        self.llm_analyzer = LLMAnalyzer(llm, self.config)

    # -- public API ---------------------------------------------------------------

    def analyze_experiences(
        self,
        conf_uid: str,
        *,
        start: float = 0.0,
        end: float = 0.0,
    ) -> List[ReflectionRecord]:
        """Analyze experiences in [start, end] (epoch; 0 = unbounded).

        Synchronous rule analysis (fast, deterministic). Returns the
        persisted records; empty input returns [] with an INFO log.
        """
        exps = self._load_experiences(conf_uid, start, end)
        if not exps:
            logger.info(f"[RFL] no experiences to analyze for {conf_uid}")
            return []
        window_start = start or min(e.finalized_at or e.started_at for e in exps)
        window_end = end or max(e.finalized_at or e.started_at for e in exps)
        out: List[ReflectionRecord] = []
        for kind in _DEFAULT_KINDS:
            try:
                rec = self.rules.analyze(
                    exps, conf_uid, reflection_type=kind,
                    window_start=window_start, window_end=window_end)
                self.reflection_repo.save(rec)
                out.append(rec)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[RFL] rule analysis '{kind}' failed: {e}")
        return out

    async def analyze_experiences_llm(
        self,
        conf_uid: str,
        *,
        start: float = 0.0,
        end: float = 0.0,
    ) -> List[ReflectionRecord]:
        """LLM refinement pass (offline/batch only). Persists at most one
        extra interaction_pattern reflection when the output validates;
        falls back to nothing (rule records already persisted) on any
        rejection."""
        exps = self._load_experiences(conf_uid, start, end)
        if not exps or not self.llm_analyzer.available():
            return []
        try:
            rec = await self.llm_analyzer.analyze(exps, conf_uid)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[RFL] llm analysis failed: {e}")
            return []
        if rec is None:
            return []
        rec.conf_uid = conf_uid
        if not rec.time_window_start:
            rec.time_window_start = min(
                e.finalized_at or e.started_at for e in exps)
            rec.time_window_end = max(
                e.finalized_at or e.started_at for e in exps)
        try:
            self.reflection_repo.save(rec)
            return [rec]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[RFL] llm reflection rejected at save: {e}")
            return []

    def analyze_last_n(self, conf_uid: str, n: int) -> List[ReflectionRecord]:
        """Analyze the n most recent experiences (rule layer)."""
        n = max(1, int(n))
        exps = self.experience_repo.list_recent(limit=n)
        if not exps:
            logger.info(f"[RFL] no recent experiences for {conf_uid}")
            return []
        window_start = min(e.finalized_at or e.started_at for e in exps)
        window_end = max(e.finalized_at or e.started_at for e in exps)
        out: List[ReflectionRecord] = []
        for kind in _DEFAULT_KINDS:
            try:
                rec = self.rules.analyze(
                    exps, conf_uid, reflection_type=kind,
                    window_start=window_start, window_end=window_end)
                self.reflection_repo.save(rec)
                out.append(rec)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[RFL] rule analysis '{kind}' failed: {e}")
        return out

    def analyze_last_n_llm(self, conf_uid: str, n: int) -> List[ReflectionRecord]:
        """Async LLM refinement over the last n experiences."""
        import asyncio
        exps = self.experience_repo.list_recent(limit=max(1, int(n)))
        if not exps or not self.llm_analyzer.available():
            return []

        async def _run() -> List[ReflectionRecord]:
            rec = await self.llm_analyzer.analyze(exps, conf_uid)
            if rec is None:
                return []
            rec.conf_uid = conf_uid
            rec.time_window_start = min(
                e.finalized_at or e.started_at for e in exps)
            rec.time_window_end = max(
                e.finalized_at or e.started_at for e in exps)
            self.reflection_repo.save(rec)
            return [rec]

        try:
            return asyncio.get_event_loop().run_until_complete(_run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_run())
            finally:
                loop.close()

    # -- internals ------------------------------------------------------------------

    def _load_experiences(
        self, conf_uid: str, start: float, end: float
    ) -> List:
        """Experiences within the window via the EXPERIENCE repository.

        The experience repo is conf-scoped (provider bound to conf_uid),
        so cross-conf data cannot leak in here.
        """
        exps = self.experience_repo.list_recent(limit=1000)
        if start or end:
            def _t(e):
                return e.finalized_at or e.started_at
            exps = [e for e in exps
                    if (not start or _t(e) >= start)
                    and (not end or _t(e) <= end)]
        return exps
