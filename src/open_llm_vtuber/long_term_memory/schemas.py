"""Data schemas for the long-term memory system.

MemoryRecord is a plain dataclass (not pydantic) to keep the module free of
framework coupling; dict conversion helpers handle serialization for the
SQLite layer and the management API.
"""

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# long-term memory types (spec section on memory types)
MEMORY_TYPES = [
    "identity",      # 身份信息
    "preference",    # 偏好（食物/口味/事物喜好厌恶）
    "habit",         # 习惯（作息/行为模式）
    "experience",    # 经历（发生过的事）
    "goal",          # 目标/计划
    "fact",          # 客观事实（工作/学校/宠物）
    "event",         # 事件（预约/约定/日程）
    "relationship",  # 关系（称呼/纪念日/里程碑）
]

# keyword categories (spec: keyword extraction categories)
KEYWORD_CATEGORIES = [
    "interest",       # 兴趣
    "skill",          # 技能
    "technology",     # 技术
    "person",         # 人
    "place",          # 地点
    "organization",   # 组织/公司/学校
    "food",           # 食物
    "hobby",          # 爱好
    "goal",           # 目标
    "project",        # 项目
    "event",          # 事件
    "preference",     # 偏好
    "topic",          # 话题
]


@dataclass
class MemoryRecord:
    """One long-term memory about the user (conf_uid-scoped)."""

    memory_id: str
    conf_uid: str
    memory_type: str          # one of MEMORY_TYPES
    content: str              # natural-language memory statement
    keywords: List[str] = field(default_factory=list)  # matching keywords
    source_history_uid: str = ""
    importance: float = 0.5   # 0.0 ~ 1.0
    confidence: float = 0.8   # 0.0 ~ 1.0
    status: str = "active"    # active / deprecated
    use_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    last_used_at: float = 0.0
    # conflict history: list of {"content": str, "reason": str, "at": float}
    history: List[Dict[str, Any]] = field(default_factory=list)

    # -- construction helpers -------------------------------------------------

    @staticmethod
    def new(
        conf_uid: str,
        memory_type: str,
        content: str,
        keywords: Optional[List[str]] = None,
        importance: float = 0.5,
        confidence: float = 0.8,
        source_history_uid: str = "",
    ) -> "MemoryRecord":
        now = time.time()
        return MemoryRecord(
            memory_id=uuid.uuid4().hex[:16],
            conf_uid=conf_uid,
            memory_type=memory_type if memory_type in MEMORY_TYPES else "fact",
            content=content.strip(),
            keywords=[k for k in (keywords or []) if k],
            source_history_uid=source_history_uid,
            importance=max(0.0, min(1.0, importance)),
            confidence=max(0.0, min(1.0, confidence)),
            status="active",
            created_at=now,
            updated_at=now,
            last_used_at=now,
            history=[],
        )

    # -- serialization --------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "MemoryRecord":
        return MemoryRecord(
            memory_id=str(d.get("memory_id", "")),
            conf_uid=str(d.get("conf_uid", "")),
            memory_type=str(d.get("memory_type", "fact")),
            content=str(d.get("content", "")),
            keywords=list(d.get("keywords") or []),
            source_history_uid=str(d.get("source_history_uid", "")),
            importance=float(d.get("importance", 0.5)),
            confidence=float(d.get("confidence", 0.8)),
            status=str(d.get("status", "active")),
            use_count=int(d.get("use_count", 0)),
            created_at=float(d.get("created_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
            last_used_at=float(d.get("last_used_at", 0.0)),
            history=list(d.get("history") or []),
        )


@dataclass
class KeywordRecord:
    """Extracted keyword with category and hit stats (retrieval booster)."""

    keyword: str
    category: str             # one of KEYWORD_CATEGORIES
    conf_uid: str
    hit_count: int = 1
    first_seen_at: float = 0.0
    last_seen_at: float = 0.0

    @staticmethod
    def new(keyword: str, category: str, conf_uid: str) -> "KeywordRecord":
        now = time.time()
        return KeywordRecord(
            keyword=keyword.strip(),
            category=category if category in KEYWORD_CATEGORIES else "topic",
            conf_uid=conf_uid,
            hit_count=1,
            first_seen_at=now,
            last_seen_at=now,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "KeywordRecord":
        return KeywordRecord(
            keyword=str(d.get("keyword", "")),
            category=str(d.get("category", "topic")),
            conf_uid=str(d.get("conf_uid", "")),
            hit_count=int(d.get("hit_count", 1)),
            first_seen_at=float(d.get("first_seen_at", 0.0)),
            last_seen_at=float(d.get("last_seen_at", 0.0)),
        )


@dataclass
class UserState:
    """Rolling snapshot of the user's conversational state (per conf_uid)."""

    conf_uid: str
    emotion: str = "neutral"      # happy/sad/angry/anxious/excited/neutral...
    energy: float = 0.5           # 0.0 ~ 1.0
    stress: float = 0.3           # 0.0 ~ 1.0
    current_topic: str = ""
    intent: str = "chat"          # chat/ask/share/complain/seek_comfort...
    updated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "UserState":
        return UserState(
            conf_uid=str(d.get("conf_uid", "")),
            emotion=str(d.get("emotion", "neutral")),
            energy=float(d.get("energy", 0.5)),
            stress=float(d.get("stress", 0.3)),
            current_topic=str(d.get("current_topic", "")),
            intent=str(d.get("intent", "chat")),
            updated_at=float(d.get("updated_at", 0.0)),
        )


@dataclass
class RelationshipMilestone:
    """Relationship memory entry (nicknames / promises / milestones)."""

    kind: str               # nickname / promise / milestone
    content: str
    created_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
