"""Conversation summary repository: summary table data access.

Phase-1A refactor: extracted verbatim from store.py. Phase-1B: typed
against the ``StorageProvider`` Protocol; the UPSERT-plus-readback pair in
``bump_turn_count`` runs inside one atomic ``transaction()`` scope.
"""

import time

from .provider import StorageProvider


class SummaryRepository:
    """Data access for the summary table (one conf_uid scope)."""

    def __init__(self, provider: StorageProvider):
        self.provider = provider

    def get_summary(self) -> str:
        row = self.provider.query_one(
            "SELECT conversation_summary FROM summary WHERE conf_uid=?",
            (self.provider.conf_uid,),
        )
        return (row["conversation_summary"] if row else "") or ""

    def save_summary(self, text: str, turn_count: int) -> None:
        self.provider.execute(
            """
            INSERT OR REPLACE INTO summary
            (conf_uid, conversation_summary, turn_count, updated_at)
            VALUES (?,?,?,?)
            """,
            (self.provider.conf_uid, text, turn_count, time.time()),
        )

    def get_turn_count(self) -> int:
        row = self.provider.query_one(
            "SELECT turn_count FROM summary WHERE conf_uid=?", (self.provider.conf_uid,)
        )
        return int(row["turn_count"]) if row else 0

    def bump_turn_count(self) -> int:
        with self.provider.transaction() as tx:
            tx.execute(
                """
                INSERT INTO summary (conf_uid, conversation_summary, turn_count, updated_at)
                VALUES (?, '', 1, ?)
                ON CONFLICT(conf_uid) DO UPDATE SET turn_count = turn_count + 1, updated_at = ?
                """,
                (self.provider.conf_uid, time.time(), time.time()),
            )
            row = tx.query_one(
                "SELECT turn_count FROM summary WHERE conf_uid=?",
                (self.provider.conf_uid,),
            )
            return int(row[0]) if row else 0
