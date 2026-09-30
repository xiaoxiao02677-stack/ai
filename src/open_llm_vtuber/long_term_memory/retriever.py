"""Memory retrieval + ranking (spec sections 17-18).

Phase 1: keyword-based retrieval over active memories, then weighted scoring:

    score = relevance*0.40 + importance*0.25 + recency*0.15
            + confidence*0.15 + usage*0.05

Only memories above min_total_score are injected, capped by
max_injected_memories and max_injection_chars. Retrieval is synchronous and
index-backed (LIKE over a small table) to stay low-latency.
"""

import re
import time
from typing import Any, Dict, List, Optional

from .schemas import MemoryRecord
from .storage.repository import MemoryRepository, KeywordRepository
from .keyword_extractor import extract_keywords, extract_search_terms
from .privacy import sanitize_for_prompt

# half-life (seconds) for the recency component — 7 days
_RECENCY_HALF_LIFE = 7 * 24 * 3600.0

# hunger/meal utterances share no terms with stored food preferences
# ("我饿了，晚饭吃什么好" vs "用户最喜欢吃火锅"); when one of these appears,
# retrieval expands the search with the user's known food/preference keywords
_HUNGER_RE = re.compile(r"饿了|好饿|吃什么|吃啥|晚饭|午饭|早饭|早餐|晚餐|午餐|夜宵|点外卖")


class MemoryRetriever:
    """Ranks and retrieves memories for prompt injection.

    Depends on the domain repositories (``MemoryRepository`` for the
    memory read surface, ``KeywordRepository`` for the hunger-bridge term
    expansion) — never on a store or a provider directly. The legacy
    ``MemoryRetriever(cfg, store)`` call still works because the facade
    subclasses MemoryRepository and carries a ``.keywords`` repository.
    """

    def __init__(self, config: Dict[str, Any], repo: MemoryRepository,
                 keywords: Optional[KeywordRepository] = None):
        self.config = config
        self.repo = repo
        # single-argument legacy form: repo must expose .keywords (the
        # MemoryStore facade does; a bare MemoryRepository doesn't need
        # hunger expansion unless wired)
        self.keywords = keywords or getattr(repo, "keywords", None)

    # -- scoring --------------------------------------------------------------

    def _relevance(self, rec: MemoryRecord, terms: List[str]) -> float:
        if not terms:
            return 0.0
        content = rec.content.lower()
        kw_blob = " ".join(k.lower() for k in rec.keywords)
        hits = 0
        for t in terms:
            tl = t.lower()
            if tl and (tl in content or tl in kw_blob):
                hits += 1
        if hits == 0:
            return 0.0
        return min(1.0, hits / max(2, len(terms) * 0.5))

    @staticmethod
    def _recency(rec: MemoryRecord, now: float) -> float:
        age = max(0.0, now - rec.updated_at)
        return 0.5 ** (age / _RECENCY_HALF_LIFE)

    @staticmethod
    def _usage(rec: MemoryRecord) -> float:
        return min(1.0, rec.use_count / 5.0)

    def score(
        self, rec: MemoryRecord, terms: List[str], now: Optional[float] = None
    ) -> float:
        now = now or time.time()
        w = (
            float(self.config.get("w_relevance", 0.40)),
            float(self.config.get("w_importance", 0.25)),
            float(self.config.get("w_recency", 0.15)),
            float(self.config.get("w_confidence", 0.15)),
            float(self.config.get("w_usage", 0.05)),
        )
        return (
            w[0] * self._relevance(rec, terms)
            + w[1] * rec.importance
            + w[2] * self._recency(rec, now)
            + w[3] * rec.confidence
            + w[4] * self._usage(rec)
        )

    # -- retrieval ------------------------------------------------------------

    # memory types that define who the user is to the companion; these are
    # always worth injecting regardless of term overlap (spec: nicknames and
    # core identity must be recallable by "你还记得我叫什么吗")
    _ALWAYS_TYPES = ("relationship", "identity")

    # categories expanded into the search when the user expresses hunger or
    # asks what to eat — bridges "晚饭吃什么" to stored 火锅/咖啡 preferences
    _HUNGER_CATEGORIES = ("food", "preference")

    def _expanded_terms(self, query_text: str, terms: List[str]) -> List[str]:
        """On hunger/meal queries, add known food/preference keywords as terms.

        "我饿了，晚饭吃什么好" shares zero terms with "用户最喜欢吃火锅",
        yet the companion should surface the preference (spec scenario A2).
        The keyword table is filled by the same utterance that stored the
        memory, so its food entries are exactly the bridge we need.
        """
        if not _HUNGER_RE.search(query_text):
            return terms
        expanded = list(terms)
        for kw in self.keywords.list_keywords(limit=100):
            if kw.category not in self._HUNGER_CATEGORIES:
                continue
            if kw.keyword not in expanded:
                expanded.append(kw.keyword)
        return expanded[:30]

    def retrieve(
        self,
        query_text: str,
        limit: Optional[int] = None,
        min_score: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Scored retrieval. Returns [{record, score, components}] sorted desc."""
        limit = limit or int(self.config.get("max_injected_memories", 5))
        min_score = (
            min_score
            if min_score is not None
            else float(self.config.get("min_total_score", 0.30))
        )
        if not query_text or not query_text.strip():
            return []
        kws = extract_keywords(query_text)
        terms = extract_search_terms(query_text, kws)
        terms = self._expanded_terms(query_text, terms)
        now = time.time()
        scored: List[Dict[str, Any]] = []
        seen_ids: set = set()

        # keyword-matched candidates first (normal path)
        if terms:
            for rec in self.repo.search_active(terms, limit=40):
                rel = self._relevance(rec, terms)
                s = self.score(rec, terms, now)
                if s >= min_score:
                    scored.append(
                        {
                            "record": rec,
                            "score": round(s, 4),
                            "components": {
                                "relevance": round(rel, 3),
                                "importance": rec.importance,
                                "recency": round(self._recency(rec, now), 3),
                                "confidence": rec.confidence,
                                "usage": round(self._usage(rec), 3),
                            },
                        }
                    )
                    seen_ids.add(rec.memory_id)

        # then always-on identity/relationship memories with high importance,
        # even when the current utterance shares no keywords with them
        for rec in self.repo.list_memories(status="active"):
            if rec.memory_id in seen_ids or rec.memory_type not in self._ALWAYS_TYPES:
                continue
            if rec.importance < 0.70:
                continue
            seen_ids.add(rec.memory_id)
            scored.append(
                {
                    "record": rec,
                    "score": round(
                        0.15 * rec.importance
                        + 0.15 * self._recency(rec, now)
                        + 0.15 * rec.confidence
                        + 0.05 * self._usage(rec),
                        4,
                    ),
                    "components": {
                        "relevance": 0.0,
                        "importance": rec.importance,
                        "recency": round(self._recency(rec, now), 3),
                        "confidence": rec.confidence,
                        "usage": round(self._usage(rec), 3),
                    },
                }
            )

        scored.sort(key=lambda x: -x["score"])
        return scored[:limit]

    def build_prompt_block(
        self, query_text: str
    ) -> Optional[Dict[str, Any]]:
        """Full pre-chat pipeline: retrieve -> rank -> build injection block.

        Returns {"block": str, "ids": [...], "scores": [...], "matched": n}
        or None when nothing relevant / on any failure (degrade silently).
        """
        try:
            results = self.retrieve(query_text)
            if not results:
                return None
            max_chars = int(self.config.get("max_injection_chars", 600))
            lines: List[str] = []
            ids: List[str] = []
            scores: List[float] = []
            used_chars = 0
            for item in results:
                rec: MemoryRecord = item["record"]
                line = f"- ({rec.memory_type}) {rec.content}"
                if used_chars + len(line) > max_chars:
                    break
                lines.append(line)
                ids.append(rec.memory_id)
                scores.append(item["score"])
                used_chars += len(line)
            if not lines:
                return None
            block = "\n".join(lines)
            return {
                "block": block,
                "ids": ids,
                "scores": scores,
                "matched": len(results),
            }
        except Exception:
            return None
