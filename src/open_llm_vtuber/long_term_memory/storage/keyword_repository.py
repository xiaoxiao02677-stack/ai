"""Keyword repository: keywords table data access.

Phase-1A refactor: extracted verbatim from store.py.
"""

import time
from typing import List

from ..schemas import KeywordRecord
from .sqlite_provider import SQLiteStorageProvider


class KeywordRepository:
    """Data access for the keywords table (one conf_uid scope)."""

    def __init__(self, provider: SQLiteStorageProvider):
        self.provider = provider

    def upsert_keyword(self, keyword: str, category: str) -> None:
        kw = keyword.strip()
        if not kw:
            return
        now = time.time()
        with self.provider._lock, self.provider._conn:
            cur = self.provider._conn.execute(
                "SELECT hit_count FROM keywords WHERE keyword=? AND category=? AND conf_uid=?",
                (kw, category, self.provider.conf_uid),
            ).fetchone()
            if cur:
                self.provider._conn.execute(
                    "UPDATE keywords SET hit_count=hit_count+1, last_seen_at=? "
                    "WHERE keyword=? AND category=? AND conf_uid=?",
                    (now, kw, category, self.provider.conf_uid),
                )
            else:
                self.provider._conn.execute(
                    "INSERT INTO keywords (keyword, category, conf_uid, hit_count, first_seen_at, last_seen_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (kw, category, self.provider.conf_uid, 1, now, now),
                )

    def list_keywords(self, limit: int = 200) -> List[KeywordRecord]:
        with self.provider._lock:
            rows = self.provider._conn.execute(
                "SELECT * FROM keywords WHERE conf_uid=? ORDER BY hit_count DESC, last_seen_at DESC LIMIT ?",
                (self.provider.conf_uid, limit),
            ).fetchall()
        return [
            KeywordRecord(
                keyword=r["keyword"],
                category=r["category"],
                conf_uid=r["conf_uid"],
                hit_count=r["hit_count"],
                first_seen_at=r["first_seen_at"],
                last_seen_at=r["last_seen_at"],
            )
            for r in rows
        ]

    def delete_keyword(self, keyword: str, category: str) -> bool:
        with self.provider._lock, self.provider._conn:
            cur = self.provider._conn.execute(
                "DELETE FROM keywords WHERE keyword=? AND category=? AND conf_uid=?",
                (keyword, category, self.provider.conf_uid),
            )
            return cur.rowcount > 0
