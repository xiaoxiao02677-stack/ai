"""Keyword repository: keywords table data access.

Phase-1A refactor: extracted verbatim from store.py. Phase-1B: typed
against the ``StorageProvider`` Protocol; the read-then-write UPSERT is
expressed as one atomic ``transaction()`` scope instead of reaching into
the provider's lock/connection.
"""

import time
from typing import List

from ..schemas import KeywordRecord
from .provider import StorageProvider


class KeywordRepository:
    """Data access for the keywords table (one conf_uid scope)."""

    def __init__(self, provider: StorageProvider):
        self.provider = provider

    def upsert_keyword(self, keyword: str, category: str) -> None:
        kw = keyword.strip()
        if not kw:
            return
        now = time.time()
        with self.provider.transaction() as tx:
            cur = tx.query_one(
                "SELECT hit_count FROM keywords WHERE keyword=? AND category=? AND conf_uid=?",
                (kw, category, self.provider.conf_uid),
            )
            if cur:
                tx.execute(
                    "UPDATE keywords SET hit_count=hit_count+1, last_seen_at=? "
                    "WHERE keyword=? AND category=? AND conf_uid=?",
                    (now, kw, category, self.provider.conf_uid),
                )
            else:
                tx.execute(
                    "INSERT INTO keywords (keyword, category, conf_uid, hit_count, first_seen_at, last_seen_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (kw, category, self.provider.conf_uid, 1, now, now),
                )

    def list_keywords(self, limit: int = 200) -> List[KeywordRecord]:
        rows = self.provider.query_rows(
            "SELECT * FROM keywords WHERE conf_uid=? ORDER BY hit_count DESC, last_seen_at DESC LIMIT ?",
            (self.provider.conf_uid, limit),
        )
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
        affected = self.provider.execute(
            "DELETE FROM keywords WHERE keyword=? AND category=? AND conf_uid=?",
            (keyword, category, self.provider.conf_uid),
        )
        return affected > 0

    def count_keywords(self) -> int:
        row = self.provider.query_one(
            "SELECT COUNT(*) AS cnt FROM keywords WHERE conf_uid=?",
            (self.provider.conf_uid,),
        )
        return int(row["cnt"]) if row else 0
