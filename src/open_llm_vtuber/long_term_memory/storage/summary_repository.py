"""Conversation summary repository: summary table data access.

Phase-1A refactor: extracted verbatim from store.py.
"""

import time

from .sqlite_provider import SQLiteStorageProvider


class SummaryRepository:
    """Data access for the summary table (one conf_uid scope)."""

    def __init__(self, provider: SQLiteStorageProvider):
        self.provider = provider

    def get_summary(self) -> str:
        with self.provider._lock:
            row = self.provider._conn.execute(
                "SELECT conversation_summary FROM summary WHERE conf_uid=?",
                (self.provider.conf_uid,),
            ).fetchone()
        return (row["conversation_summary"] if row else "") or ""

    def save_summary(self, text: str, turn_count: int) -> None:
        with self.provider._lock, self.provider._conn:
            self.provider._conn.execute(
                """
                INSERT OR REPLACE INTO summary
                (conf_uid, conversation_summary, turn_count, updated_at)
                VALUES (?,?,?,?)
                """,
                (self.provider.conf_uid, text, turn_count, time.time()),
            )

    def get_turn_count(self) -> int:
        with self.provider._lock:
            row = self.provider._conn.execute(
                "SELECT turn_count FROM summary WHERE conf_uid=?", (self.provider.conf_uid,)
            ).fetchone()
        return int(row["turn_count"]) if row else 0

    def bump_turn_count(self) -> int:
        with self.provider._lock, self.provider._conn:
            self.provider._conn.execute(
                """
                INSERT INTO summary (conf_uid, conversation_summary, turn_count, updated_at)
                VALUES (?, '', 1, ?)
                ON CONFLICT(conf_uid) DO UPDATE SET turn_count = turn_count + 1, updated_at = ?
                """,
                (self.provider.conf_uid, time.time(), time.time()),
            )
            row = self.provider._conn.execute(
                "SELECT turn_count FROM summary WHERE conf_uid=?", (self.provider.conf_uid,)
            ).fetchone()
            return int(row[0]) if row else 0
