"""StrategyAnalyzer: derive condition→recommendation guidelines from lessons.

Two backends, mirroring the Reflection/Lesson analyzers:

* ``RuleAnalyzer`` — deterministic (no LLM): groups lessons by their
  metadata-analyzer + support signal; a cluster of ``min_lessons``
  same-source lessons yields one strategy whose condition is the
  shared reflection context and whose recommendation is the lesson
  consensus, phrased advisorially. Below the threshold: nothing (a
  single lesson does not warrant a strategy).
* ``LLMAnalyzer`` — optional refinement with the same extractor call
  convention as LTM/Reflection/Lesson. Output JSON-validated; hard
  commanding language, foreign lesson ids, or missing fields are
  rejected with a logged warning.

Strategies GUIDE, never command — and nothing in this phase feeds them
back into prompts or behavior.
"""

import json
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from ..long_term_memory.privacy import privacy_check, sanitize_memory_text
from ..lesson.schemas import LessonRecord
from .schemas import StrategyRecord

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class RuleAnalyzer:
    """Deterministic strategy: lesson clusters become guidelines."""

    def __init__(self, min_lessons: int = 2):
        # how many same-source lessons are needed to earn a strategy
        self.min_lessons = min_lessons

    def analyze(
        self,
        lessons: Sequence[LessonRecord],
        conf_uid: str,
    ) -> List[StrategyRecord]:
        # group lessons by the reflection they came from: two lessons
        # pointing at the same reflection = corroborated signal
        by_reflection: Dict[str, List[LessonRecord]] = defaultdict(list)
        for lsn in lessons:
            for rid in lsn.source_reflection_ids:
                by_reflection[rid].append(lsn)

        out: List[StrategyRecord] = []
        for rid, cluster in by_reflection.items():
            if len(cluster) < self.min_lessons:
                continue
            joined = [l.lesson for l in cluster
                      if privacy_check(l.lesson)["ok"]]
            if len(joined) < self.min_lessons:
                continue
            rec = StrategyRecord.new(
                conf_uid, [l.lesson_id for l in cluster])
            rec.condition = sanitize_memory_text(
                f"当互动数据再次出现以下观察时：{joined[0][:80]}", 300)
            rec.recommendation = sanitize_memory_text(
                "；".join(joined[:3]), 600)
            rec.evidence = [l.lesson_id for l in cluster]
            rec.confidence = min(
                0.95, sum(l.confidence for l in cluster) / len(cluster))
            rec.metadata = {
                "analyzer": "rules",
                "source_reflection_id": rid,
                "cluster_size": len(cluster),
            }
            try:
                rec.validate()
                out.append(rec)
            except ValueError as e:
                logger.warning(f"[STR] rule strategy rejected: {e}")
        return out


class LLMAnalyzer:
    """Optional LLM distillation; same call convention as LTM extraction."""

    def __init__(self, llm, config: Dict[str, Any]):
        self.llm = llm
        self.config = config

    def available(self) -> bool:
        # no new config key per spec: default follows reflection/lesson style
        return self.llm is not None and bool(
            self.config.get("strategy", {}).get("llm_analysis", True))

    async def analyze(
        self,
        lessons: Sequence[LessonRecord],
        conf_uid: str,
    ) -> Optional[StrategyRecord]:
        """Return a validated strategy or None (caller falls back)."""
        if not self.available() or not lessons:
            return None
        batch = int(self.config.get("strategy", {}).get("batch_size", 20))
        sample = list(lessons)[:batch]
        payload = [
            {"id": l.lesson_id, "lesson": l.lesson[:200],
             "confidence": l.confidence}
            for l in sample
            if privacy_check(l.lesson)["ok"]
        ]
        if not payload:
            logger.warning("[STR] all lessons privacy-filtered; skipping LLM")
            return None

        messages = [{
            "role": "user",
            "content": (
                "以下是若干条经验教训。请提炼一条『情境→做法』指南："
                "condition 描述适用情境，recommendation 给出温和建议。"
                "禁止‘必须/务必/一定要/策略’等命令式表述，"
                "禁止编造数据之外的信息。只输出 JSON："
                '{"condition": "...", "recommendation": "...", '
                '"source_lesson_ids": ["<教训id>", ...], "confidence": 0.0-1.0}\n'
                f"Lessons:\n{json.dumps(payload, ensure_ascii=False)}"
            ),
        }]
        raw = await self._call(messages)
        if not raw:
            return None
        return self._parse(raw, [l.lesson_id for l in sample])

    async def _call(self, messages: List[Dict[str, str]]) -> Optional[str]:
        pieces: List[str] = []
        try:
            import asyncio
            timeout = float(self.config.get("strategy", {}).get(
                "llm_timeout", self.config.get("extraction_timeout", 60.0)))
            stream = self.llm.chat_completion(messages, _STRATEGY_SYSTEM_PROMPT)

            async def _consume() -> None:
                async for event in stream:
                    if isinstance(event, str):
                        pieces.append(event)
                    elif isinstance(event, dict):
                        if event.get("type") == "text_delta":
                            pieces.append(event.get("text", ""))
                        elif event.get("type") == "error":
                            logger.warning(
                                f"[STR] llm stream error: {event.get('message')}")

            await asyncio.wait_for(_consume(), timeout=timeout)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[STR] llm call failed: {e}")
            return None
        return "".join(pieces).strip()

    def _parse(self, raw: str, valid_ids: List[str]) -> Optional[StrategyRecord]:
        try:
            cleaned = _JSON_FENCE.sub("", raw).strip()
            data = json.loads(cleaned)
            sources = [str(x) for x in (data.get("source_lesson_ids") or [])]
            sources = [x for x in sources if x in valid_ids]
            record = StrategyRecord.new(
                conf_uid="__pending__",  # engine overwrites with the real conf
                source_lesson_ids=sources or None,
            )
            record.condition = str(data.get("condition", "")).strip()
            record.recommendation = str(data.get("recommendation", "")).strip()
            record.evidence = list(sources)
            record.confidence = float(data.get("confidence", 0.5))
            record.metadata = {"analyzer": "llm"}
            record.validate()  # boundary + traceability checks
            return record
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"[STR] llm output rejected: {e}")
            return None


_STRATEGY_SYSTEM_PROMPT = (
    "你是互动指南提炼器。基于给定的经验教训输出一条『情境→做法』指南，"
    "语气为温和建议，禁止命令式与硬性表述，"
    "禁止编造数据之外的信息。输出必须是合法 JSON 对象。"
)
