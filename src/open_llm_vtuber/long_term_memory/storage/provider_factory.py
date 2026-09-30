"""Provider factory: config-driven backend selection (sqlite | hermes).

Phase 3: the composition point. ``create_storage_provider(config)`` reads
the provider choice from the LTM config dict (same JSON the /memory panel
edits — no second config parser) and returns the concrete
``StorageProvider``. Callers (``MemoryStore``) never see the class name.

Config shape (merged over ``_DEFAULT_CONFIG`` in the module facade):

    "storage": {
        "provider": "sqlite",          # sqlite | hermes
        "hermes": {
            "base_url": "http://127.0.0.1:12396",
            "user_id": "vtuber",
            "timeout": 10.0
            # api_key: via LTM_HERMES_API_KEY env var, never in the file
        }
    }

Unknown provider names fail fast with a clear message (no silent
fallback to SQLite — the user must know which backend they run).
"""

from typing import Any, Dict

from .provider import StorageProvider
from .sqlite_provider import SQLiteStorageProvider

import os

PROVIDER_SQLITE = "sqlite"
PROVIDER_HERMES = "hermes"


def create_storage_provider(conf_uid: str, config: Dict[str, Any] | None = None) -> StorageProvider:
    """Build the configured storage backend for one conf_uid.

    ``config`` is the LTM module config (``get_config()``); when None the
    default SQLite backend is used (backward compatibility: no config,
    no behavior change).
    """
    storage_cfg = (config or {}).get("storage") or {}
    name = str(storage_cfg.get("provider", PROVIDER_SQLITE)).strip().lower()

    if name == PROVIDER_SQLITE:
        return SQLiteStorageProvider(conf_uid)

    if name == PROVIDER_HERMES:
        hermes_cfg = storage_cfg.get("hermes") or {}
        base_url = (hermes_cfg.get("base_url")
                    or os.environ.get("LTM_HERMES_BASE_URL", "")
                    or "http://127.0.0.1:12396").strip()
        user_id = (hermes_cfg.get("user_id")
                   or os.environ.get("LTM_HERMES_USER_ID", "vtuber")).strip()
        try:
            timeout = float(hermes_cfg.get("timeout", 10.0))
        except (TypeError, ValueError):
            timeout = 10.0
        # secrets: env var only, never persisted in memory_config.json
        api_key = os.environ.get("LTM_HERMES_API_KEY") or None
        # fail fast when the backend is down (acceptance §12): a config
        # that says hermes must fail loudly at startup, never silently
        # degrade into "no memory"; tests building mock transports pass
        # verify_on_start=False explicitly
        verify_on_start = bool(hermes_cfg.get("verify_on_start", True))

        from .hermes_provider import HermesStorageProvider  # lazy: only
        # this factory branch ever imports the hermes adapter
        return HermesStorageProvider(
            conf_uid, base_url=base_url, user_id=user_id,
            timeout=timeout, api_key=api_key,
            verify=verify_on_start)

    raise ValueError(
        f"unknown storage provider '{name}' (expected "
        f"'{PROVIDER_SQLITE}' or '{PROVIDER_HERMES}')")
