"""ActionAnalyzer: derive an abstract intent from a selected decision.

The analyzer NEVER re-selects strategies, re-ranks candidates or
re-evaluates anything — the Decision already chose. It only shapes
the intent:

* ``RuleActionAnalyzer`` — deterministic (no LLM): picks a conservative
  action_type from the decision's shape (selected -> ACKNOWLEDGE/
  RESPOND by default with a documented rule) and builds structured,
  non-executable parameters from the strategy's own condition/
  recommendation text (truncated, data-only).
* ``LLMActionAnalyzer`` — optional refinement over the established
  LTM streaming protocol. The LLM may ONLY output action_type /
  parameters / reason; every provenance id (decision/evaluation/
  strategy) is derived by the ENGINE from the decision record —
  fake ids from the LLM are structurally impossible to accept
  because they are never read. Illegal action types, executable
  parameter payloads, malformed JSON and timeouts are all rejected;
  failure NEVER masquerades as planned.
"""

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from ..long_term_memory.privacy import privacy_check, sanitize_memory_text
from ..decision.schemas import (DecisionRecord, STATUS_SELECTED)
from ..strategy.schemas import StrategyRecord
from .schemas import (ActionIntentRecord, ACTION_TYPES, STATUS_PLANNED,
                      STATUS_REJECTED)

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# rule-layer default: a selected decision expresses a respond-style intent
_RULE_DEFAULT_TYPE = "RESPOND"


class RuleActionAnalyzer:
    """Deterministic intent shaping (never re-decides anything)."""

    def analyze(self, decision: DecisionRecord,
                strategy: StrategyRecord) -> ActionIntentRecord:
        rec = ActionIntentRecord.new(
            decision.conf_uid, decision.decision_id, _RULE_DEFAULT_TYPE)
        rec.evaluation_id = decision.selected_evaluation_id
        rec.strategy_id = decision.selected_strategy_id
        if decision.status == STATUS_SELECTED:
            rec.status = STATUS_PLANNED
            rec.reason = sanitize_memory_text(
                f"决策选择了该方案；记录以{ _RULE_DEFAULT_TYPE.lower() }"
                f"形式回应的意图。", 300)
            rec.parameters = {
                "style_hint": sanitize_memory_text(
                    strategy.recommendation or "", 200),
                "condition_hint": sanitize_memory_text(
                    strategy.condition or "", 120),
            }
        else:
            # engine normally never reaches here for non-selected; keep the
            # analyzer total anyway: non-selected -> rejected intent
            rec.status = STATUS_REJECTED
            rec.reason = sanitize_memory_text(
                f"决策状态为 {decision.status}，不产生计划意图。", 200)
        rec.evidence = [decision.selected_evaluation_id]
        rec.metadata = {"analyzer": "rules"}
        return rec


class LLMActionAnalyzer:
    """Optional LLM refinement; same call convention as LTM extraction.

    The LLM output can only shape action_type/parameters/reason —
    provenance ids are NEVER taken from the LLM (they come from the
    decision record in the engine), so fake ids are structurally
    unrepresentable.
    """

    def __init__(self, llm, config: Dict[str, Any]):
        self.llm = llm
        self.config = config

    def available(self) -> bool:
        return self.llm is not None and bool(
            self.config.get("action", {}).get("llm_analysis", True))

    async def analyze(
        self,
        decision: DecisionRecord,
        strategy: StrategyRecord,
    ) -> Optional[ActionIntentRecord]:
        """Return a validated intent shaped by the LLM, or None (fall back)."""
        if not self.available() or decision.status != STATUS_SELECTED:
            return None
        if not privacy_check(strategy.condition + strategy.recommendation)["ok"]:
            logger.warning("[ACT] strategy text privacy-filtered; skipping LLM")
            return None
        messages = [{
            "role": "user",
            "content": (
                "基于以下已选定的情境与建议，生成一个抽象的行动意图。"
                "action_type 只能是 RESPOND / REMIND / ACKNOWLEDGE 之一。"
                "parameters 是纯数据（如语气提示），禁止任何可执行内容。"
                "只输出 JSON："
                '{"action_type": "...", "parameters": {"style_hint": "..."}, '
                '"reason": "..."}\n'
                f"情境：{strategy.condition[:150]}\n"
                f"建议：{strategy.recommendation[:150]}"
            ),
        }]
        raw = await self._call(messages)
        if not raw:
            return None
        return self._parse(raw, decision)

    async def _call(self, messages: List[Dict[str, str]]) -> Optional[str]:
        pieces: List[str] = []
        try:
            import asyncio
            timeout = float(self.config.get("action", {}).get(
                "llm_timeout", self.config.get("extraction_timeout", 60.0)))
            stream = self.llm.chat_completion(messages, _ACTION_SYSTEM_PROMPT)

            async def _consume() -> None:
                async for event in stream:
                    if isinstance(event, str):
                        pieces.append(event)
                    elif isinstance(event, dict):
                        if event.get("type") == "text_delta":
                            pieces.append(event.get("text", ""))
                        elif event.get("type") == "error":
                            logger.warning(
                                f"[ACT] llm stream error: {event.get('message')}")

            await asyncio.wait_for(_consume(), timeout=timeout)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[ACT] llm call failed: {e}")
            return None
        return "".join(pieces).strip()

    def _parse(self, raw: str,
               decision: DecisionRecord) -> Optional[ActionIntentRecord]:
        try:
            cleaned = _JSON_FENCE.sub("", raw).strip()
            data = json.loads(cleaned)
            action_type = str(data.get("action_type", ""))
            if action_type not in ACTION_TYPES:
                logger.warning(
                    f"[ACT] llm returned illegal action_type '{action_type}'; "
                    f"rejected")
                return None
            params = data.get("parameters") or {}
            if not isinstance(params, dict):
                logger.warning("[ACT] llm parameters not a dict; rejected")
                return None
            rec = ActionIntentRecord.new(
                decision.conf_uid, decision.decision_id, action_type)
            rec.evaluation_id = decision.selected_evaluation_id
            rec.strategy_id = decision.selected_strategy_id  # engine-derived
            rec.parameters = params
            rec.status = STATUS_PLANNED
            rec.reason = str(data.get("reason", "")).strip()[:300]
            rec.evidence = [decision.selected_evaluation_id]
            rec.metadata = {"analyzer": "llm"}
            rec.validate()  # enum + param blacklist + provenance presence
            return rec
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"[ACT] llm output rejected: {e}")
            return None


_ACTION_SYSTEM_PROMPT = (
    "你是行动意图生成器。基于给定的情境与建议输出一个抽象意图"
    "（仅 RESPOND/REMIND/ACKNOWLEDGE），parameters 为纯数据，"
    "禁止任何可执行内容与命令式表述。输出必须是合法 JSON 对象。"
)
