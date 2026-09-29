"""LLM-based memory extraction (async, non-blocking).

Uses the agent's own stateless LLM instance when available (provider-agnostic:
works with OpenAI-compatible str streams and Claude dict event streams).
Falls back to rule-based extraction when no LLM is available or the LLM call
fails — chat is never blocked or broken by extraction problems.
"""

import asyncio
import json
import re
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from .schemas import MemoryRecord, MEMORY_TYPES
from .keyword_extractor import extract_keywords
from .privacy import privacy_check, sanitize_memory_text, is_probably_injection

_EXTRACTION_SYSTEM_PROMPT = """你是一个记忆提取器，从用户与AI伴侣的对话中提取值得长期记住的用户记忆。

规则：
1. 只提取关于用户的稳定、重要、可复用的信息；不要提取寒暄、临时状态、天气、无意义内容。
2. 每条记忆用第三人称陈述句，简洁自然（中文不超过40字）。
3. memory_type 必须是这些之一：identity, preference, habit, experience, goal, fact, event, relationship。
4. importance 0.0-1.0：影响用户生活/关系亲密度的程度。核心身份、强烈偏好、重要目标 > 一般事实。
5. confidence 0.0-1.0：这是用户的真实稳定状态的可能性。注意用户说"可能"、"好像"、"猜测"时降低；用户直接否定时（"我现在不喝了"）表示状态更新而非新记忆。
6. 天气、临时情绪、"今天好累"这类内容不要提取。
7. 关系类记忆（relationship）：称呼、约定、承诺、纪念日、里程碑。
8. 如果对话没有值得记住的内容，返回空数组 []。

输出严格的 JSON 数组（不要 markdown 代码块）：
[{"memory_type": "...", "content": "...", "importance": 0.0, "confidence": 0.0}]"""

# interrogative content is never a stable memory — a recall question like
# 我叫什么名字吗 must not become an identity record
_INTERROGATIVE_TAIL = re.compile(r"[?？]\s*$|[吗呢]\s*$")
_INTERROGATIVE_HEAD = re.compile(r"^(什么|怎么|为什么|是不是|有没有|哪个|哪些|谁|几)")
_INTERROGATIVE_ANY = re.compile(r"什么名字|叫什么|多少钱|几岁|多少")

# transient states (weather, mood-of-the-day) are not long-term memories
# (spec rule 6: 天气/临时情绪/"今天好累" 不要提取)
_TRANSIENT_RE = re.compile(r"下雨|下雪|好累|好困|好烦|心情不好|饿了|困了")


class LLMExtractor:
    """Extracts memory candidates from a conversation turn via LLM."""

    def __init__(self, config: Dict[str, Any], llm=None):
        self.config = config
        self.llm = llm

    def available(self) -> bool:
        return self.llm is not None

    async def _call_llm(self, user_text: str, ai_text: str) -> Optional[str]:
        """One non-streaming-equivalent call; returns raw text or None.

        Handles both stream protocols:
          - OpenAI-compatible: yields str chunks / tool-call lists
          - Claude: yields {"type": "text_delta", "text": ...} events
        """
        if self.llm is None:
            return None
        messages = [
            {
                "role": "user",
                "content": (
                    f"用户说：{user_text[:500]}\n\nAI回复：{ai_text[:300]}\n\n"
                    "请提取值得长期记住的用户记忆，输出 JSON 数组（无则 []）。"
                ),
            }
        ]
        pieces: List[str] = []
        try:
            timeout = float(self.config.get("extraction_timeout", 60.0))
            stream = self.llm.chat_completion(messages, _EXTRACTION_SYSTEM_PROMPT)
            async def _consume() -> None:
                async for event in stream:
                    if isinstance(event, str):
                        if event == "__API_NOT_SUPPORT_TOOLS__":
                            continue
                        pieces.append(event)
                    elif isinstance(event, list):
                        continue  # tool calls: extraction doesn't use tools
                    elif isinstance(event, dict):
                        if event.get("type") == "text_delta":
                            pieces.append(event.get("text", ""))
                        elif event.get("type") == "error":
                            logger.warning(f"[LTM] extractor LLM error event: {event.get('message', '')[:120]}")
            await asyncio.wait_for(_consume(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("[LTM] extraction LLM call timed out")
            return None
        except Exception as e:
            logger.warning(f"[LTM] extraction LLM call failed: {e}")
            return None
        raw = "".join(pieces).strip()
        # reasoning models emit <think>...</think>; keep only visible output
        if "<think>" in raw:
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        return raw or None

    @staticmethod
    def _parse_json_array(raw: str) -> List[Dict[str, Any]]:
        """Robustly parse the LLM's JSON array output."""
        if not raw:
            return []
        text = raw.strip()
        # strip markdown fences
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        # find first [ ... last ]
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            logger.warning(f"[LTM] unparseable extraction output: {raw[:200]}")
            return []
        if not isinstance(data, list):
            logger.warning(f"[LTM] extraction output not a JSON array: {raw[:120]}")
            return []
        return [d for d in data if isinstance(d, dict)]

    async def extract(
        self,
        conf_uid: str,
        user_text: str,
        ai_text: str,
        source_history_uid: str = "",
    ) -> List[MemoryRecord]:
        """Main entry: returns validated MemoryRecord candidates."""
        if not user_text or len(user_text) < int(
            self.config.get("extraction_min_chars", 4)
        ):
            return []

        # rule-based keywords first (always stored as keyword hits)
        kws = extract_keywords(user_text)

        candidates: List[Dict[str, Any]] = []
        raw = await self._call_llm(user_text, ai_text or "")
        if raw:
            candidates = self._parse_json_array(raw)
            if self.llm is not None:
                logger.info(
                    f"[LTM] LLM extraction: {len(candidates)} candidate(s) "
                    f"from {len(raw)} chars of raw output"
                )
        if not candidates:
            # rule-based fallback extraction from explicit patterns
            candidates = self._rule_fallback(user_text)

        records: List[MemoryRecord] = []
        min_importance = float(self.config.get("min_importance_to_store", 0.35))
        for cand in candidates:
            content = sanitize_memory_text(str(cand.get("content", "")))
            if not content or is_probably_injection(content):
                continue
            # questions are not memories ("我叫什么名字吗" must not be stored)
            if _INTERROGATIVE_TAIL.search(content) or _INTERROGATIVE_HEAD.match(content) or _INTERROGATIVE_ANY.search(content):
                logger.debug(f"[LTM] interrogative content rejected: {content[:40]}")
                continue
            # weather / momentary mood are transient, not long-term (rule 6)
            if _TRANSIENT_RE.search(content):
                logger.debug(f"[LTM] transient content rejected: {content[:40]}")
                continue
            if privacy_check(content)["ok"] is False:
                logger.debug(f"[LTM] privacy filter rejected: {content[:40]}")
                continue
            mtype = str(cand.get("memory_type", "fact"))
            if mtype not in MEMORY_TYPES:
                mtype = "fact"
            try:
                importance = float(cand.get("importance", 0.5))
            except (TypeError, ValueError):
                importance = 0.5
            try:
                confidence = float(cand.get("confidence", 0.8))
            except (TypeError, ValueError):
                confidence = 0.8
            # identity/relationship (nickname, core identity) must stay above
            # the retriever's always-on injection gate (importance >= 0.70)
            if mtype in ("identity", "relationship"):
                importance = max(importance, 0.75)
            if importance < min_importance:
                continue
            matched_kw = [k["keyword"] for k in kws if k["keyword"] in content]
            records.append(
                MemoryRecord.new(
                    conf_uid=conf_uid,
                    memory_type=mtype,
                    content=content,
                    keywords=matched_kw,
                    importance=max(0.0, min(1.0, importance)),
                    confidence=max(0.0, min(1.0, confidence)),
                    source_history_uid=source_history_uid,
                )
            )
        return records

    # -- rule fallback --------------------------------------------------------

    @staticmethod
    def _rule_fallback(user_text: str) -> List[Dict[str, Any]]:
        """No-LLM fallback: catch the most explicit 'remember me' patterns."""
        out: List[Dict[str, Any]] = []
        rules = [
            (r"我(?:最)?(?:喜欢|爱吃|超爱)([^，。！？!?,\s]{1,12})", "preference", 0.7, 0.8),
            (r"我(?:讨厌|不喜欢|不吃)([^，。！？!?,\s]{1,12})", "preference", 0.6, 0.8),
            (r"我(?:叫|的名字是)([^，。！？!?,\s]{1,12})", "identity", 0.9, 0.9),
            (r"叫我([^，。！？!?,\s]{1,10})", "relationship", 0.85, 0.9),
            # pets: 我(家)养了一只猫，名字叫小白 / 我养了一只狗叫旺财
            (r"我(?:家)?(?:养|有)了?一只?(猫|狗)(?:，?名字叫|，?名叫|，?叫)?([^，。！？!?,\s]{0,4})", "fact", 0.65, 0.85),
            # beverages: 迷上了喝咖啡 / 爱上了茶 / 习惯喝咖啡
            (r"(?:迷上|爱上|喜欢|习惯)(?:了)?喝?(咖啡|茶|奶茶|拿铁|可乐|果汁|酒)", "preference", 0.65, 0.85),
            (r"每天一杯(拿铁|咖啡|奶茶)", "preference", 0.6, 0.85),
            # explicit state updates: 我现在不喝咖啡了，改喝茶了
            (r"不(?:喝|吃|用)(咖啡|茶|奶茶|拿铁|可乐)(?:了)?", "preference", 0.75, 0.9),
            (r"改(?:喝|吃)(咖啡|茶|奶茶|拿铁)(?:了)?", "preference", 0.75, 0.9),
            (r"我在学(?:习|)([^，。！？!?,\s]{1,12})", "fact", 0.7, 0.8),
            (r"我想(?:要|去)?([^，。！？!?,\s]{2,14})", "goal", 0.5, 0.6),
        ]
        for pat, mtype, imp, conf in rules:
            for m in re.finditer(pat, user_text):
                if not m.groups() or not (m.group(1) or "").strip():
                    if not (mtype == "fact" and m.lastindex and m.lastindex >= 2):
                        continue
                if mtype == "fact" and m.lastindex and m.lastindex >= 2:
                    # pet rule: build a clean statement from animal + name
                    animal = (m.group(1) or "").strip()
                    name = (m.group(2) or "").strip()
                    # separator remnants / degree-adverb fillers are not names
                    name = re.sub(r"^(?:名字叫|名叫|叫)", "", name)
                    if re.match(r"^(?:特别|非常|超级|真的|很|超|太|好)", name):
                        name = ""
                    if not animal and not name:
                        continue
                    content = f"用户养了一只{animal}" if animal else "用户养了宠物"
                    if name:
                        content += f"叫{name}"
                elif mtype in ("identity", "relationship"):
                    captured = (m.group(1) or "").strip()
                    if not captured:
                        continue
                    content = (
                        f"用户希望被称为{captured}"
                        if mtype == "relationship"
                        else f"用户叫{captured}"
                    )
                else:
                    if not (m.group(1) or "").strip() and m.lastindex:
                        continue
                    # third-person statement from the first-person phrase
                    phrase = re.sub(r"^我", "", m.group(0).strip())
                    if phrase.startswith("每天一杯"):
                        content = f"用户每天喝{phrase[2:]}"
                    else:
                        content = f"用户{phrase}"
                out.append(
                    {
                        "memory_type": mtype,
                        "content": content,
                        "importance": imp,
                        "confidence": conf,
                    }
                )
        # de-dup by content
        seen = set()
        deduped = []
        for c in out:
            if c["content"] not in seen:
                seen.add(c["content"])
                deduped.append(c)
        return deduped[:5]
