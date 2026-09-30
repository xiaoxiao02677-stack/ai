"""EvaluationAnalyzer: judge whether a strategy applies to a context.

* ``RuleAnalyzer`` — deterministic, explainable (no NLP framework):
  normalized keyword overlap between the strategy's condition and the
  current context. ``condition_match`` = matched keywords / condition
  keywords; ``applicable`` requires at least one hit AND match >=
  0.15. The reason lists what matched — every judgment is auditable.
* ``LLMAnalyzer`` — optional refinement over the SAME extractor call
  convention as LTM/Reflection/Lesson/Strategy (stream protocol,
  timeout, privacy gating, JSON validation). Rejections: external
  strategy_id, missing id, malformed JSON, commanding language, out-
  of-range scores. LLM failure degrades explicitly to the rule layer
  (logged warning) — same non-silent fallback discipline as the other
  domains.

An evaluation is a JUDGMENT. Nothing here executes, injects, or
mutates anything.
"""

import json
import re
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from ..long_term_memory.privacy import privacy_check
from ..strategy.schemas import StrategyRecord
from .schemas import EvaluationRecord

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_TOKEN = re.compile(r"[\w\u4e00-\u9fff]+")

# rule-layer thresholds (documented, deterministic)
APPLICABLE_MIN_MATCH = 0.15   # fraction of condition keywords hit
APPLICABLE_MIN_HITS = 1       # at least one keyword must hit


def _keywords(text: str) -> List[str]:
    """Deterministic tokenization: CJK bigrams + word tokens, lowercased."""
    if not text:
        return []
    tokens: List[str] = []
    for word in _TOKEN.findall(text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            if len(word) == 1:
                tokens.append(word)
            else:
                tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
        else:
            tokens.append(word)
    return tokens


class RuleAnalyzer:
    """Deterministic context↔condition keyword-overlap evaluation."""

    def evaluate(self, strategy: StrategyRecord,
                 context: str) -> EvaluationRecord:
        rec = EvaluationRecord.new(strategy.conf_uid, strategy.strategy_id)
        cond_kws = _keywords(strategy.condition)
        ctx_kws = set(_keywords(context or ""))
        matched = [k for k in cond_kws if k in ctx_kws]
        rec.condition_match = (len(matched) / len(cond_kws)) if cond_kws else 0.0
        rec.applicable = (len(matched) >= APPLICABLE_MIN_HITS
                          and rec.condition_match >= APPLICABLE_MIN_MATCH)
        # relevance blends condition coverage with the strategy's own
        # confidence (how well-founded the guideline itself is)
        rec.relevance = round(
            0.7 * rec.condition_match + 0.3 * strategy.confidence, 4)
        rec.confidence = round(
            (0.5 + 0.5 * rec.condition_match)
            * (0.6 + 0.4 * strategy.confidence), 4)
        rec.evidence = matched[:10]
        if not context or not context.strip():
            rec.reason = "上下文为空，无法判断适用性"
            rec.applicable = False
            rec.condition_match = 0.0
            rec.relevance = 0.0
        elif matched:
            rec.reason = (f"命中条件关键词 {len(matched)}/{len(cond_kws)}"
                          f"（{'、'.join(matched[:5])}）")
        else:
            rec.reason = f"未命中任何条件关键词（共 {len(cond_kws)} 个）"
        rec.metadata = {"analyzer": "rules",
                        "condition_keywords": len(cond_kws)}
        return rec


class LLMAnalyzer:
    """Optional LLM refinement; same call convention as LTM extraction."""

    def __init__(self, llm, config: Dict[str, Any]):
        self.llm = llm
        self.config = config

    def available(self) -> bool:
        return self.llm is not None and bool(
            self.config.get("evaluation", {}).get("llm_analysis", True))

    async def evaluate(
        self,
        strategy: StrategyRecord,
        context: str,
    ) -> Optional[EvaluationRecord]:
        """Return a validated evaluation or None (caller falls back to rules)."""
        if not self.available():
            return None
        if not privacy_check(context or "")["ok"]:
            logger.warning("[EVL] context privacy-filtered; skipping LLM")
            return None
        messages = [{
            "role": "user",
            "content": (
                "判断以下指南（condition）与当前对话情境的匹配程度。"
                "只输出判断结果，禁止任何命令式表述。只输出 JSON："
                '{"applicable": true/false, "relevance": 0.0-1.0, '
                '"confidence": 0.0-1.0, "reason": "..."}\n'
                f"指南条件：{strategy.condition[:200]}\n"
                f"当前情境：{(context or '')[:400]}"
            ),
        }]
        raw = await self._call(messages)
        if not raw:
            return None
        return self._parse(raw, strategy)

    async def _call(self, messages: List[Dict[str, str]]) -> Optional[str]:
        pieces: List[str] = []
        try:
            import asyncio
            timeout = float(self.config.get("evaluation", {}).get(
                "llm_timeout", self.config.get("extraction_timeout", 60.0)))
            stream = self.llm.chat_completion(messages, _EVALUATION_SYSTEM_PROMPT)

            async def _consume() -> None:
                async for event in stream:
                    if isinstance(event, str):
                        pieces.append(event)
                    elif isinstance(event, dict):
                        if event.get("type") == "text_delta":
                            pieces.append(event.get("text", ""))
                        elif event.get("type") == "error":
                            logger.warning(
                                f"[EVL] llm stream error: {event.get('message')}")

            await asyncio.wait_for(_consume(), timeout=timeout)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[EVL] llm call failed: {e}")
            return None
        return "".join(pieces).strip()

    def _parse(self, raw: str,
               strategy: StrategyRecord) -> Optional[EvaluationRecord]:
        try:
            cleaned = _JSON_FENCE.sub("", raw).strip()
            data = json.loads(cleaned)
            rec = EvaluationRecord.new(strategy.conf_uid, strategy.strategy_id)
            rec.applicable = bool(data.get("applicable", False))
            rec.relevance = float(data.get("relevance", 0.0))
            rec.confidence = float(data.get("confidence", 0.0))
            rec.reason = str(data.get("reason", "")).strip()[:300]
            rec.metadata = {"analyzer": "llm"}
            rec.validate()  # score bounds + language boundary
            return rec
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"[EVL] llm output rejected: {e}")
            return None


_EVALUATION_SYSTEM_PROMPT = (
    "你是情境匹配判断器。基于给定的指南条件与当前情境输出匹配判断，"
    "语气为事实描述，禁止命令式与硬性表述，禁止编造信息。"
    "输出必须是合法 JSON 对象。"
)
