"""LessonAnalyzer: distill reusable lessons from reflection records.

Two backends, mirroring the Reflection analyzer:

* ``RuleAnalyzer`` — deterministic (no LLM): turns statistical
  reflections into a phrased takeaway. Only fires when a reflection
  carries strong enough signal (enough source experiences); otherwise
  returns nothing (a weak observation does not deserve a lesson).
* ``LLMAnalyzer`` — optional refinement with the same extractor call
  convention as LTM/Reflection. Output is JSON-validated; hard-policy
  language ("必须/must/always/策略") or untraceable source ids are
  rejected with a logged warning; the caller falls back to rules/no-op.

Lessons INFORM, they never COMMAND — and nothing in this phase feeds
them back into prompts or behavior.
"""

import json
import re
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from ..long_term_memory.privacy import privacy_check, sanitize_memory_text
from ..reflection.schemas import ReflectionRecord
from .schemas import LessonRecord

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class RuleAnalyzer:
    """Deterministic lesson: statistical reflections phrased as takeaways."""

    def __init__(self, min_support: int = 5):
        # how many source experiences a reflection needs to earn a lesson
        self.min_support = min_support

    def analyze(
        self,
        reflections: Sequence[ReflectionRecord],
        conf_uid: str,
    ) -> List[LessonRecord]:
        out: List[LessonRecord] = []
        for rfl in reflections:
            support = len(rfl.source_experience_ids)
            if support < self.min_support:
                continue  # weak observation, not lesson-worthy yet
            if not privacy_check(rfl.observation)["ok"]:
                continue
            rec = LessonRecord.new(conf_uid, [rfl.reflection_id])
            rec.lesson = sanitize_memory_text(
                f"基于 {support} 次互动的观察：{rfl.observation}", 600)
            rec.confidence = min(0.95, rfl.confidence)
            rec.metadata = {"analyzer": "rules", "support": support,
                            "reflection_type": rfl.reflection_type}
            try:
                rec.validate()
                out.append(rec)
            except ValueError as e:
                logger.warning(f"[LSN] rule lesson rejected: {e}")
        return out


class LLMAnalyzer:
    """Optional LLM distillation; same call convention as LTM extraction."""

    def __init__(self, llm, config: Dict[str, Any]):
        self.llm = llm
        self.config = config

    def available(self) -> bool:
        return self.llm is not None and bool(
            self.config.get("lesson", {}).get("llm_analysis", True))

    async def analyze(
        self,
        reflections: Sequence[ReflectionRecord],
        conf_uid: str,
    ) -> Optional[LessonRecord]:
        """Return a validated lesson or None (caller falls back)."""
        if not self.available() or not reflections:
            return None
        batch = int(self.config.get("lesson", {}).get("batch_size", 20))
        sample = list(reflections)[:batch]
        payload = [
            {"id": r.reflection_id, "reflection_type": r.reflection_type,
             "observation": r.observation[:200],
             "support": len(r.source_experience_ids)}
            for r in sample
            if privacy_check(r.observation)["ok"]
        ]
        if not payload:
            logger.warning("[LSN] all reflections privacy-filtered; skipping LLM")
            return None

        messages = [{
            "role": "user",
            "content": (
                "以下是用户互动的反思观察（含观察文本与支撑互动次数）。"
                "请总结出一条可复用的教训：只陈述事实与温和建议，"
                "禁止‘必须/务必/策略/always/must’等命令式表述，"
                "禁止添加数据之外的信息。只输出 JSON："
                '{"lesson": "...", "source_reflection_ids": ["<反思id>", ...], '
                '"confidence": 0.0-1.0}\n'
                f"Reflections:\n{json.dumps(payload, ensure_ascii=False)}"
            ),
        }]
        raw = await self._call(messages)
        if not raw:
            return None
        return self._parse(raw, [r.reflection_id for r in sample])

    async def _call(self, messages: List[Dict[str, str]]) -> Optional[str]:
        pieces: List[str] = []
        try:
            import asyncio
            timeout = float(self.config.get("lesson", {}).get(
                "llm_timeout", self.config.get("extraction_timeout", 60.0)))
            stream = self.llm.chat_completion(messages, _LESSON_SYSTEM_PROMPT)

            async def _consume() -> None:
                async for event in stream:
                    if isinstance(event, str):
                        pieces.append(event)
                    elif isinstance(event, dict):
                        if event.get("type") == "text_delta":
                            pieces.append(event.get("text", ""))
                        elif event.get("type") == "error":
                            logger.warning(
                                f"[LSN] llm stream error: {event.get('message')}")

            await asyncio.wait_for(_consume(), timeout=timeout)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[LSN] llm call failed: {e}")
            return None
        return "".join(pieces).strip()

    def _parse(self, raw: str, valid_ids: List[str]) -> Optional[LessonRecord]:
        try:
            cleaned = _JSON_FENCE.sub("", raw).strip()
            data = json.loads(cleaned)
            text = str(data.get("lesson", "")).strip()
            sources = [str(x) for x in (data.get("source_reflection_ids") or [])]
            sources = [x for x in sources if x in valid_ids]
            record = LessonRecord.new(
                conf_uid="__pending__",  # engine overwrites with the real conf
                source_reflection_ids=sources or None,
            )
            record.lesson = text
            record.confidence = float(data.get("confidence", 0.5))
            record.metadata = {"analyzer": "llm"}
            record.validate()  # boundary + traceability checks
            return record
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"[LSN] llm output rejected: {e}")
            return None


_LESSON_SYSTEM_PROMPT = (
    "你是经验教训提炼器。基于给定的反思观察输出一条可复用的教训，"
    "语气为事实陈述或温和建议，禁止命令式与策略性表述，"
    "禁止编造数据之外的信息。输出必须是合法 JSON 对象。"
)
