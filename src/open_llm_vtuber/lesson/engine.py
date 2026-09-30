"""LessonEngine: distill and persist lessons from stored reflections.

Phase 6 orchestration: pulls ReflectionRecords through the REFLECTION
repository (never the raw provider), runs the rule analyzer (always,
cheap) and optionally the LLM analyzer, persists resulting
LessonRecords through the LESSON repository. Offline/batch only —
nothing in the conversation path calls this; lessons are stored for
future use, never auto-injected into prompts or behavior.

Empty input is a no-op with an INFO log. All failures log and skip.
"""

from typing import List, Optional

from loguru import logger

from ..reflection.repository import ReflectionRepository
from .analyzer import LLMAnalyzer, RuleAnalyzer
from .repository import LessonRepository
from .schemas import LessonRecord


class LessonEngine:
    """Distill and persist reusable lessons from a conf's reflections."""

    def __init__(self, lesson_repo: LessonRepository,
                 reflection_repo: ReflectionRepository,
                 config: Optional[dict] = None, llm=None):
        self.lesson_repo = lesson_repo
        self.reflection_repo = reflection_repo
        self.config = config or {}
        self.rules = RuleAnalyzer(
            min_support=int(self.config.get("lesson", {}).get(
                "min_support", 5)))
        self.llm_analyzer = LLMAnalyzer(llm, self.config)

    # -- public API -----------------------------------------------------------

    def analyze_reflections(self, conf_uid: str, limit: int = 200) -> List[LessonRecord]:
        """Rule-layer distillation over recent reflections (sync, fast)."""
        reflections = self.reflection_repo.list_recent(limit=limit)
        if not reflections:
            logger.info(f"[LSN] no reflections to distill for {conf_uid}")
            return []
        out: List[LessonRecord] = []
        for rec in self.rules.analyze(reflections, conf_uid):
            try:
                self.lesson_repo.save(rec)
                out.append(rec)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[LSN] lesson save failed: {e}")
        return out

    async def analyze_reflections_llm(
        self, conf_uid: str, limit: int = 200
    ) -> List[LessonRecord]:
        """LLM distillation pass (offline/batch only). At most one lesson
        per call; rejections leave the rule-layer records untouched."""
        reflections = self.reflection_repo.list_recent(limit=limit)
        if not reflections or not self.llm_analyzer.available():
            return []
        try:
            rec = await self.llm_analyzer.analyze(reflections, conf_uid)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[LSN] llm analysis failed: {e}")
            return []
        if rec is None:
            return []
        rec.conf_uid = conf_uid
        try:
            self.lesson_repo.save(rec)
            return [rec]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[LSN] llm lesson rejected at save: {e}")
            return []
