"""DecisionAnalyzer: form an explainable decision from evaluations.

* ``RuleDecisionAnalyzer`` — deterministic, first-class citizen (no
  LLM). Eligibility gate: applicable AND confidence >= MIN_CONFIDENCE.
  Ranking: relevance (primary) → confidence (tie-break) →
  condition_match (tie-break). If the top TWO eligible candidates tie
  on ALL three keys the decision is ABSTAIN ("ambiguous candidates") —
  never a random or insertion-order pick. No eligible candidate →
  ABSTAIN with an explicit reason.
* ``LLMDecisionAnalyzer`` — optional refinement BEHIND the rule layer,
  reusing the established LTM streaming protocol. The LLM may only
  pick one of the PASSED evaluation ids (whitelist) or null (= abstain);
  external evaluation ids, external strategy ids, rewritten scores,
  malformed JSON are all rejected. On any rejection/failure the rule
  decision stands (explicit, logged).

Decisions are RECORDS. Nothing here executes anything — the chain
ends at Decision.
"""

import json
import re
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from ..long_term_memory.privacy import privacy_check
from ..evaluation.schemas import EvaluationRecord
from .schemas import (DecisionRecord, STATUS_ABSTAIN, STATUS_SELECTED)

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# deterministic, documented thresholds (no global config system for one knob)
MIN_CONFIDENCE = 0.5   # eligible: applicable AND confidence >= this
TIE_EPSILON = 1e-9     # float tolerance for "same score" detection


class RuleDecisionAnalyzer:
    """Deterministic multi-evaluation decision with abstain semantics."""

    def decide(self, evaluations: Sequence[EvaluationRecord],
               conf_uid: str) -> DecisionRecord:
        rec = DecisionRecord.new(conf_uid, STATUS_ABSTAIN)
        evs = list(evaluations)
        rec.evidence = [e.evaluation_id for e in evs]
        rec.metadata = {"analyzer": "rules", "n_candidates": len(evs)}

        if not evs:
            rec.reason = "没有可用的评估结果，放弃选择。"
            return rec

        eligible = [e for e in evs
                    if e.applicable and e.confidence >= MIN_CONFIDENCE]
        if not eligible:
            n_false = sum(1 for e in evs if not e.applicable)
            n_low = len(evs) - n_false
            rec.reason = (f"无满足门槛的候选：{n_false} 条不适用、"
                          f"{n_low} 条置信度不足，放弃选择。")
            return rec

        def _key(e: EvaluationRecord):
            # primary relevance, secondary confidence, tertiary match
            return (e.relevance, e.confidence, e.condition_match)

        ranked = sorted(eligible, key=_key, reverse=True)
        top = ranked[0]
        if len(ranked) >= 2:
            second = ranked[1]
            tied = all(abs(a - b) < TIE_EPSILON
                       for a, b in zip(_key(top), _key(second)))
            if tied:
                rec.reason = (f"两个候选 relevance/confidence/match 完全接近"
                              f"（{top.relevance:.2f} vs {second.relevance:.2f}），"
                              f"无法区分，放弃选择（ambiguous candidates）。")
                rec.metadata["ambiguous_ids"] = [
                    top.evaluation_id, second.evaluation_id]
                return rec

        rec.status = STATUS_SELECTED
        rec.selected_evaluation_id = top.evaluation_id
        rec.selected_strategy_id = top.strategy_id
        rec.confidence = round(
            0.5 * top.confidence + 0.5 * top.relevance, 4)
        rec.reason = (f"该评估在候选中同时具有较高 relevance（{top.relevance:.2f}）"
                      f"与 confidence（{top.confidence:.2f}），"
                      f"condition_match（{top.condition_match:.2f}）高于或区分于其他候选。")
        rec.metadata["selected_scores"] = {
            "relevance": top.relevance,
            "confidence": top.confidence,
            "condition_match": top.condition_match,
        }
        return rec


class LLMDecisionAnalyzer:
    """Optional LLM refinement; same call convention as LTM extraction."""

    def __init__(self, llm, config: Dict[str, Any]):
        self.llm = llm
        self.config = config

    def available(self) -> bool:
        return self.llm is not None and bool(
            self.config.get("decision", {}).get("llm_analysis", True))

    async def decide(
        self,
        evaluations: Sequence[EvaluationRecord],
        conf_uid: str,
    ) -> Optional[DecisionRecord]:
        """Return a validated decision or None (caller keeps the rule one)."""
        if not self.available() or not evaluations:
            return None
        payload = [
            {"evaluation_id": e.evaluation_id,
             "applicable": e.applicable,
             "relevance": e.relevance,
             "confidence": e.confidence,
             "reason": e.reason[:120]}
            for e in evaluations
            if privacy_check(e.reason)["ok"]
        ]
        if not payload:
            logger.warning("[DEC] all evaluations privacy-filtered; skipping LLM")
            return None
        messages = [{
            "role": "user",
            "content": (
                "以下是若干条对同一情境的策略评估。请选择最相关的一条评估，"
                "或返回 null 表示放弃选择。禁止编造信息，禁止命令式表述。"
                "只输出 JSON："
                '{"selected_evaluation_id": "<评估id>" 或 null, '
                '"reason": "..."}\n'
                f"Evaluations:\n{json.dumps(payload, ensure_ascii=False)}"
            ),
        }]
        raw = await self._call(messages)
        if not raw:
            return None
        return self._parse(raw, list(evaluations), conf_uid)

    async def _call(self, messages: List[Dict[str, str]]) -> Optional[str]:
        pieces: List[str] = []
        try:
            import asyncio
            timeout = float(self.config.get("decision", {}).get(
                "llm_timeout", self.config.get("extraction_timeout", 60.0)))
            stream = self.llm.chat_completion(messages, _DECISION_SYSTEM_PROMPT)

            async def _consume() -> None:
                async for event in stream:
                    if isinstance(event, str):
                        pieces.append(event)
                    elif isinstance(event, dict):
                        if event.get("type") == "text_delta":
                            pieces.append(event.get("text", ""))
                        elif event.get("type") == "error":
                            logger.warning(
                                f"[DEC] llm stream error: {event.get('message')}")

            await asyncio.wait_for(_consume(), timeout=timeout)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[DEC] llm call failed: {e}")
            return None
        return "".join(pieces).strip()

    def _parse(self, raw: str, evaluations: Sequence[EvaluationRecord],
               conf_uid: str) -> Optional[DecisionRecord]:
        try:
            cleaned = _JSON_FENCE.sub("", raw).strip()
            data = json.loads(cleaned)
            by_id = {e.evaluation_id: e for e in evaluations}
            sel = data.get("selected_evaluation_id")
            reason = str(data.get("reason", "")).strip()[:300]

            if sel is None:
                # LLM chose to abstain — a legitimate outcome
                rec = DecisionRecord.new(conf_uid, STATUS_ABSTAIN)
                rec.evidence = [e.evaluation_id for e in evaluations]
                rec.reason = reason or "LLM 判断无足够强的候选，放弃选择。"
                rec.metadata = {"analyzer": "llm"}
                rec.validate()
                return rec

            if str(sel) not in by_id:
                logger.warning(
                    f"[DEC] llm returned external evaluation id '{sel}'; rejected")
                return None
            chosen = by_id[str(sel)]
            rec = DecisionRecord.new(conf_uid, STATUS_SELECTED)
            rec.selected_evaluation_id = chosen.evaluation_id
            rec.selected_strategy_id = chosen.strategy_id  # from the evaluation, never the LLM
            rec.confidence = round(
                0.5 * chosen.confidence + 0.5 * chosen.relevance, 4)
            rec.reason = reason or "LLM 选出最相关评估。"
            rec.evidence = [e.evaluation_id for e in evaluations]
            rec.metadata = {"analyzer": "llm"}
            rec.validate()
            return rec
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"[DEC] llm output rejected: {e}")
            return None


_DECISION_SYSTEM_PROMPT = (
    "你是决策辅助器。基于给定的策略评估选择最相关的一条或放弃，"
    "语气为事实描述，禁止命令式与硬性表述，禁止编造信息。"
    "输出必须是合法 JSON 对象。"
)
