"""StrategyEngine: distill and persist strategies from stored lessons.

Phase 7 orchestration: pulls LessonRecords through the LESSON
repository (never the raw provider), runs the rule analyzer (always,
cheap) and optionally the LLM analyzer, persists resulting
StrategyRecords through the STRATEGY repository. Offline/batch only —
nothing in the conversation path calls this; strategies are stored for
future use, never auto-injected into prompts or behavior.

Method names follow the Phase-7 spec (analyze_reflections /
analyze_reflections_llm), even though the input domain is lessons —
the names mean "analyze the reflection-derived layers".

Empty input is a no-op with an INFO log. All failures log and skip.
"""

from typing import List, Optional

from loguru import logger

from ..lesson.repository import LessonRepository
from .analyzer import LLMAnalyzer, RuleAnalyzer
from .repository import StrategyRepository
from .schemas import StrategyRecord


class StrategyEngine:
    """Distill and persist condition→recommendation guidelines."""

    def __init__(self, strategy_repo: StrategyRepository,
                 lesson_repo: LessonRepository,
                 config: Optional[dict] = None, llm=None):
        self.strategy_repo = strategy_repo
        self.lesson_repo = lesson_repo
        self.config = config or {}
        self.rules = RuleAnalyzer(
            min_lessons=int(self.config.get("strategy", {}).get(
                "min_lessons", 2)))
        self.llm_analyzer = LLMAnalyzer(llm, self.config)

    # -- public API -----------------------------------------------------------

    def analyze_reflections(self, conf_uid: str,
                            limit: int = 200) -> List[StrategyRecord]:
        """Rule-layer distillation over recent lessons (sync, fast)."""
        lessons = self.lesson_repo.list_lessons(limit=limit)
        if not lessons:
            logger.info(f"[STR] no lessons to distill for {conf_uid}")
            return []
        out: List[StrategyRecord] = []
        for rec in self.rules.analyze(lessons, conf_uid):
            try:
                self.strategy_repo.save(rec)
                out.append(rec)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[STR] strategy save failed: {e}")
        return out

    async def analyze_reflections_llm(
        self, conf_uid: str, limit: int = 200
    ) -> List[StrategyRecord]:
        """LLM distillation pass (offline/batch only). At most one strategy
        per call; rejections leave the rule-layer records untouched."""
        lessons = self.lesson_repo.list_lessons(limit=limit)
        if not lessons or not self.llm_analyzer.available():
            return []
        try:
            rec = await self.llm_analyzer.analyze(lessons, conf_uid)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[STR] llm analysis failed: {e}")
            return []
        if rec is None:
            return []
        rec.conf_uid = conf_uid
        try:
            self.strategy_repo.save(rec)
            return [rec]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[STR] llm strategy rejected at save: {e}")
            return []
