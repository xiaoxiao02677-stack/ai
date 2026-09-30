"""ReflectionAnalyzer: derive fact-only observations from experiences.

Two backends:

* ``RuleAnalyzer`` — deterministic statistics (no LLM): interaction
  pattern, frequency analysis, outcome distribution, tool usage. The
  engine ALWAYS runs this layer; its output is guaranteed valid.
* ``LLMAnalyzer`` — optional refinement when an LLM is attached (config
  ``reflection.llm_analysis``): same extractor call convention as LTM
  (async chat_completion stream + timeout), output validated against the
  ReflectionRecord schema and REJECTED when it carries lesson/strategy
  language or lacks evidence traceability. LLM failure degrades to the
  rule layer — logged, never raised into callers.

No observation is invented: every number comes from the passed
ExperienceRecords, every claim carries ``evidence`` + 
``source_experience_ids``.
"""

import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from ..long_term_memory.privacy import privacy_check, sanitize_memory_text
from ..experience.schemas import ExperienceRecord
from .schemas import REFLECTION_TYPES, ReflectionRecord

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# how the rule layer words its numbers — fact templates, no advice
_TEMPLATES = {
    "interaction_pattern": (
        "在 {n} 条互动经历中，最常见的话题是「{topic}」（出现 {k} 次）；"
        "这些互动的结果中 {outcome} 占 {pct}%。"),
    "frequency_analysis": (
        "观察窗口内共有 {n} 条互动经历，其中 {ai_ok} 条 AI 正常回复，"
        "{ai_err} 条 AI 出错，{empty} 条无文本回复。"),
    "outcome_distribution": (
        "互动结果分布：{dist}。"),
    "tool_usage": (
        "共记录 {tool_n} 次工具调用，涉及 {tool_kinds} 个工具；"
        "最常用工具为「{top_tool}」（{top_n} 次）。"),
}


def _topic_of(rec: ExperienceRecord) -> str:
    """Cheap topic proxy: first keyword-ish span of the user input."""
    text = sanitize_memory_text(rec.user_input or "", 40)
    if not text:
        return "(空输入)"
    return text[:12]


class RuleAnalyzer:
    """Deterministic reflection: pure statistics over experiences."""

    def analyze(
        self,
        experiences: Sequence[ExperienceRecord],
        conf_uid: str,
        *,
        reflection_type: str = "interaction_pattern",
        window_start: float = 0.0,
        window_end: float = 0.0,
    ) -> ReflectionRecord:
        exps = list(experiences)
        rec = ReflectionRecord.new(
            conf_uid, reflection_type,
            [e.experience_id for e in exps])
        rec.time_window_start = window_start
        rec.time_window_end = window_end
        rec.evidence = [e.experience_id for e in exps]

        if not exps:
            rec.observation = "观察窗口内没有可分析的互动经历。"
            rec.confidence = 0.0
            return rec

        if reflection_type == "frequency_analysis":
            ai_ok = sum(1 for e in exps if e.outcome_type == "turn_complete")
            ai_err = sum(1 for e in exps if e.outcome_type == "ai_error")
            empty = sum(1 for e in exps if e.outcome_type == "empty_reply")
            rec.observation = _TEMPLATES["frequency_analysis"].format(
                n=len(exps), ai_ok=ai_ok, ai_err=ai_err, empty=empty)
        elif reflection_type == "outcome_distribution":
            dist = Counter(e.outcome_type for e in exps)
            parts = "、".join(f"{k} {v} 条" for k, v in dist.most_common())
            rec.observation = _TEMPLATES["outcome_distribution"].format(dist=parts)
        elif reflection_type == "tool_usage":
            tools: List[str] = []
            for e in exps:
                tools.extend(t.get("tool_name", "unknown")
                             for t in e.tool_calls)
            if not tools:
                rec.observation = "观察窗口内的互动没有发生工具调用。"
            else:
                dist = Counter(tools)
                top, top_n = dist.most_common(1)[0]
                rec.observation = _TEMPLATES["tool_usage"].format(
                    tool_n=len(tools), tool_kinds=len(dist),
                    top_tool=top, top_n=top_n)
        else:  # interaction_pattern (default)
            topics = Counter(_topic_of(e) for e in exps)
            topic, k = topics.most_common(1)[0]
            outcomes = Counter(e.outcome_type for e in exps)
            top_outcome, top_n = outcomes.most_common(1)[0]
            rec.observation = _TEMPLATES["interaction_pattern"].format(
                n=len(exps), topic=topic, k=k,
                outcome=top_outcome, pct=round(100 * top_n / len(exps)))

        rec.confidence = 0.9 if len(exps) >= 5 else max(0.4, 0.1 * len(exps) + 0.3)
        rec.metadata = {"analyzer": "rules", "n_experiences": len(exps)}
        return rec


class LLMAnalyzer:
    """Optional LLM refinement; same call convention as LTM extraction.

    Strictly offline/batch (called by the engine, never from the chat
    path). Output is JSON-validated; lesson/strategy language or
    fabricated claims are rejected with a logged warning and the caller
    falls back to the rule layer.
    """

    def __init__(self, llm, config: Dict[str, Any]):
        self.llm = llm
        self.config = config

    def available(self) -> bool:
        return self.llm is not None and bool(
            self.config.get("reflection", {}).get("llm_analysis", True))

    async def analyze(
        self,
        experiences: Sequence[ExperienceRecord],
        conf_uid: str,
    ) -> Optional[ReflectionRecord]:
        """Return a validated reflection or None (caller falls back)."""
        if not self.available() or not experiences:
            return None
        batch = self.config.get("reflection", {}).get("batch_size", 20)
        exps = list(experiences)[: int(batch)]
        payload = [
            {"user_input": e.user_input[:120], "ai_response": e.ai_response[:160],
             "outcome": e.outcome_type, "id": e.experience_id}
            for e in exps
            if privacy_check(e.user_input + e.ai_response)["ok"]
        ]
        if not payload:
            logger.warning("[RFL] all experiences privacy-filtered; skipping LLM")
            return None

        messages = [{
            "role": "user",
            "content": (
                "请作为分析师，基于以下对话体验数据总结它们的共同模式。"
                "只报告事实观察，禁止任何建议/策略/‘下次应该’类内容。"
                "只输出 JSON："
                '{"reflection_type": "interaction_pattern", '
                '"observation": "...", "evidence": ["<体验id>", ...], '
                '"confidence": 0.0-1.0}\n'
                f"Experiences:\n{json.dumps(payload, ensure_ascii=False)}"
            ),
        }]
        raw = await self._call(messages)
        if not raw:
            return None
        parsed = self._parse(raw, [e["id"] for e in payload])
        return parsed

    async def _call(self, messages: List[Dict[str, str]]) -> Optional[str]:
        pieces: List[str] = []
        try:
            timeout = float(self.config.get("reflection", {}).get(
                "llm_timeout", self.config.get("extraction_timeout", 60.0)))
            import asyncio
            stream = self.llm.chat_completion(
                messages, _REFLECTION_SYSTEM_PROMPT)
            async def _consume() -> None:
                async for event in stream:
                    if isinstance(event, str):
                        pieces.append(event)
                    elif isinstance(event, dict):
                        if event.get("type") == "text_delta":
                            pieces.append(event.get("text", ""))
                        elif event.get("type") == "error":
                            logger.warning(
                                f"[RFL] llm stream error: {event.get('message')}")
            await asyncio.wait_for(_consume(), timeout=timeout)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[RFL] llm call failed, rule fallback: {e}")
            return None
        return "".join(pieces).strip()

    def _parse(self, raw: str, valid_ids: List[str]) -> Optional[ReflectionRecord]:
        try:
            cleaned = _JSON_FENCE.sub("", raw).strip()
            data = json.loads(cleaned)
            obs = str(data.get("observation", "")).strip()
            evidence = [str(x) for x in (data.get("evidence") or [])]
            evidence = [x for x in evidence if x in valid_ids]
            record = ReflectionRecord.new(
                conf_uid="__pending__",  # engine overwrites with the real conf
                reflection_type=str(data.get("reflection_type", "interaction_pattern")),
                source_experience_ids=evidence,
            )
            record.observation = obs
            record.evidence = evidence
            record.confidence = float(data.get("confidence", 0.5))
            record.metadata = {"analyzer": "llm"}
            record.validate()  # boundary check: fact-only, traceable
            if not evidence:
                logger.warning("[RFL] llm output has no valid evidence ids; rejected")
                return None
            return record
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"[RFL] llm output rejected: {e}")
            return None


_REFLECTION_SYSTEM_PROMPT = (
    "你是互动数据分析师。你只能输出基于给定体验数据的事实观察，"
    "禁止编造数据之外的信息，禁止任何建议、策略、劝告或‘下次应该如何’的表述。"
    "输出必须是合法 JSON 对象。"
)
