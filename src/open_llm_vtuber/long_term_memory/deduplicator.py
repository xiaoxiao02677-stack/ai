"""Deduplication + conflict resolution (spec sections 15-16).

Dedup:   near-identical active memories are reinforced instead of duplicated
         (importance up, confidence up, use_count up, keywords merged).
Conflict: same-topic opposite-content updates mark the old record deprecated
         (status='deprecated', history preserved) and store the new one.
"""

import re
import time
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from .schemas import MemoryRecord

_DEDUP_SIMILARITY = 0.82       # content similarity threshold for "same memory"
_CONFLICT_SIMILARITY = 0.55    # below dedup but same type + shared keywords

# types whose records describe mutable user state (conflict-eligible)
_MUTABLE_TYPES = ("preference", "habit", "goal", "fact")

# explicit state-change markers: the new memory negates or replaces an
# earlier stance ("不喝咖啡了", "改喝茶", "戒了"). The LLM rewrites freely
# ("迷上喝咖啡" -> "不喝咖啡了，改喝茶"), so surface similarity can fall
# below the conflict band even for a genuine contradiction.
_STATE_CHANGE_RE = re.compile(
    r"不(?:再)?(?:喝|吃|用|玩|看|喜欢|想|做)"
    r"|改(?:喝|吃|用|玩|看|做|成)"
    r"|戒(?:了|掉|烟|酒|糖|咖啡|奶茶|游戏)"
    r"|换成"
)

# generic words that must NOT count as a shared subject for the explicit
# state-change path ("不喜欢香菜" shares 喜欢 with "喜欢火锅" but does
# not contradict it)
_GENERIC_SUBJECT_WORDS = {"喜欢", "爱", "讨厌", "想要", "打算", "准备", "计划", "最近", "现在"}


class MemoryDeduplicator:
    @staticmethod
    def _similarity(a: str, b: str) -> float:
        if a == b:
            return 1.0
        return SequenceMatcher(None, a, b).ratio()

    @staticmethod
    def _merge_keywords(a: List[str], b: List[str]) -> List[str]:
        merged = list(a)
        for k in b:
            if k not in merged:
                merged.append(k)
        return merged[:12]

    def find_duplicate(
        self, existing: List[MemoryRecord], candidate: MemoryRecord
    ) -> Optional[MemoryRecord]:
        """Return the existing record considered the same memory, if any."""
        for rec in existing:
            if rec.memory_type != candidate.memory_type:
                continue
            if rec.content == candidate.content:
                return rec
            if self._similarity(rec.content, candidate.content) >= _DEDUP_SIMILARITY:
                return rec
        return None

    def reinforce(self, rec: MemoryRecord, candidate: MemoryRecord) -> MemoryRecord:
        """Reinforce an existing memory hit again by the user.

        importance up (capped 1.0), confidence up (capped 1.0),
        use_count +1, keywords merged, timestamp refreshed.
        """
        rec.importance = min(1.0, rec.importance + 0.05)
        rec.confidence = min(1.0, rec.confidence + 0.05)
        rec.use_count += 1
        rec.keywords = self._merge_keywords(rec.keywords, candidate.keywords)
        rec.updated_at = time.time()
        return rec


class MemoryConflictResolver:
    @staticmethod
    def _subject_overlap(a: MemoryRecord, b: MemoryRecord) -> bool:
        """Two memories talk about the same subject (keywords overlap)."""
        ka = {k.lower() for k in a.keywords if k}
        kb = {k.lower() for k in b.keywords if k}
        if ka and kb and (ka & kb):
            return True
        return False

    @staticmethod
    def _specific_subject_overlap(a: MemoryRecord, b: MemoryRecord) -> bool:
        """Subject overlap that excludes generic words like 喜欢.

        Also falls back to content-substring overlap when either keyword
        list is empty (LLM candidates sometimes arrive with no keywords).
        """
        ka = {k.lower() for k in a.keywords if k and k not in _GENERIC_SUBJECT_WORDS}
        kb = {k.lower() for k in b.keywords if k and k not in _GENERIC_SUBJECT_WORDS}
        if ka and kb and (ka & kb):
            return True
        # keyword lists may be empty; a shared concrete noun inside both
        # contents is still a shared subject (e.g. 咖啡)
        ca = {w for w in re.findall(r"[\u4e00-\u9fffA-Za-z]+", a.content) if len(w) >= 2 and w not in _GENERIC_SUBJECT_WORDS and w not in ("用户",)}
        cb = {w for w in re.findall(r"[\u4e00-\u9fffA-Za-z]+", b.content) if len(w) >= 2 and w not in _GENERIC_SUBJECT_WORDS and w not in ("用户",)}
        return bool(ca & cb)

    def find_conflict(
        self, existing: List[MemoryRecord], candidate: MemoryRecord
    ) -> Optional[MemoryRecord]:
        """Return an active memory that the candidate contradicts.

        Two trigger paths:
        1. Similarity band: same memory_type + subject overlap + similarity
           in [0.55, 0.82) — the classic near-rewrite contradiction.
        2. Explicit state change: the candidate contains a state-change
           marker (不喝…了 / 改喝… / 戒了…) and shares a concrete subject
           with the old record, even when the LLM rewrote both texts so
           freely that surface similarity fell below the band, or typed
           them slightly differently (habit vs preference — both mutable
           user state).
        """
        dedup = MemoryDeduplicator()
        explicit_change = bool(_STATE_CHANGE_RE.search(candidate.content))
        for rec in existing:
            if rec.memory_id == candidate.memory_id:
                continue
            if rec.memory_type not in _MUTABLE_TYPES:
                # conflicts mainly occur on mutable-state types
                continue
            if candidate.memory_type not in _MUTABLE_TYPES:
                continue
            if not self._subject_overlap(rec, candidate):
                continue
            sim = dedup._similarity(rec.content, candidate.content)
            # path 1: near-rewrite contradiction — same type only, or
            # complementary facts ("在学Python" vs "准备找实习") sitting in
            # the band would falsely conflict
            if (
                rec.memory_type == candidate.memory_type
                and _CONFLICT_SIMILARITY <= sim < _DEDUP_SIMILARITY
            ):
                return rec
            # path 2: explicit negation/replacement of a shared subject.
            # The old record must be a plain stance (no state-change marker
            # of its own) so a freshly stored "改喝茶" isn't re-deprecated
            # by a companion candidate from the same extraction batch.
            if (
                explicit_change
                and sim < _CONFLICT_SIMILARITY
                and not _STATE_CHANGE_RE.search(rec.content)
                and self._specific_subject_overlap(rec, candidate)
            ):
                return rec
        return None

    def resolve(
        self, old: MemoryRecord, new: MemoryRecord, reason: str = "状态更新"
    ) -> Tuple[MemoryRecord, MemoryRecord]:
        """Mark old deprecated (keep history), return updated pair.

        The old record keeps all its content in `history` so the management
        UI can show the change timeline (spec: 咖啡冲突场景保留历史).
        """
        old.status = "deprecated"
        old.history = old.history or []
        old.history.append(
            {
                "event": "deprecated_by",
                "reason": reason,
                "replaced_by_id": new.memory_id,
                "replaced_by_content": new.content,
                "at": time.time(),
            }
        )
        old.updated_at = time.time()
        new.history = new.history or []
        new.history.append(
            {
                "event": "replaces",
                "reason": reason,
                "replaced_id": old.memory_id,
                "replaced_content": old.content,
                "at": time.time(),
            }
        )
        return old, new
