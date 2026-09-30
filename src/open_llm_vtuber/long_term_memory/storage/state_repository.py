"""UserState repository: user_state table data access.

Phase-1A refactor: extracted verbatim from store.py. Phase-1B: typed
against the ``StorageProvider`` Protocol and expressed through provider
primitives; only the ``updated_at`` stamp (a domain rule) remains here.
"""

import time
from typing import Optional

from ..schemas import UserState
from .provider import StorageProvider


class StateRepository:
    """Data access for the user_state table (one conf_uid scope)."""

    def __init__(self, provider: StorageProvider):
        self.provider = provider

    def get_state(self) -> Optional[UserState]:
        row = self.provider.query_one(
            "SELECT * FROM user_state WHERE conf_uid=?", (self.provider.conf_uid,)
        )
        if not row:
            return None
        return UserState(
            conf_uid=row["conf_uid"],
            emotion=row["emotion"] or "neutral",
            energy=float(row["energy"] or 0.5),
            stress=float(row["stress"] or 0.3),
            current_topic=row["current_topic"] or "",
            intent=row["intent"] or "chat",
            updated_at=float(row["updated_at"] or 0.0),
        )

    def save_state(self, state: UserState) -> None:
        state.updated_at = time.time()
        self.provider.execute(
            """
            INSERT OR REPLACE INTO user_state
            (conf_uid, emotion, energy, stress, current_topic, intent, updated_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                state.conf_uid,
                state.emotion,
                state.energy,
                state.stress,
                state.current_topic,
                state.intent,
                state.updated_at,
            ),
        )
