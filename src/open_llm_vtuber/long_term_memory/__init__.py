"""
Long-Term Memory System for Open-LLM-VTuber (AI Companion Memory)
===================================================================

Minimal-invasion memory engine living between the conversation layer
and the prompt builder:

  retrieval:   single_conversation.py  ->  metadata["ltm_context"] (pre-chat)
  injection:   basic_memory_agent.py   ->  user-role bracketed block in _to_messages
  extraction:  single_conversation.py  ->  asyncio.create_task (post-reply, async)
  management:  config/memory_panel.py  ->  /memory/api + /memory static page

All failures degrade gracefully: chat keeps working without memory.

Storage: stdlib-backed, one store per conf_uid under long_term_memory_data/
(backend details are an internal concern of the storage subpackage).
Phase 1 uses keyword-based retrieval; embeddings are a phase-2 upgrade.
"""

import asyncio
import json
import os
from typing import Any, Dict, List, Optional

from loguru import logger

from .store import MemoryStore
from .manager import MemoryManager

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(os.getcwd(), "long_term_memory_data")

# module-scoped config: deliberately outside conf.yaml so config_sync
# (which strips unknown keys on startup) never touches it
_CONFIG_PATH = os.path.join(_MODULE_DIR, "memory_config.json")

_DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    # retrieval
    "max_injected_memories": 5,
    "min_total_score": 0.30,
    "max_injection_chars": 600,
    # ranking weights (relevance/importance/recency/confidence/usage)
    "w_relevance": 0.40,
    "w_importance": 0.25,
    "w_recency": 0.15,
    "w_confidence": 0.15,
    "w_usage": 0.05,
    # extraction
    "extraction_min_chars": 4,
    "min_importance_to_store": 0.35,
    "extraction_timeout": 60.0,
    "extraction_cooldown": 1.0,
    # short-term summary
    "summary_interval": 20,
    # user state
    "state_update_interval": 5,
    # privacy: never store content matching these patterns
    "privacy_patterns": [
        "sk-[A-Za-z0-9]{8,}",
        "ghp_[A-Za-z0-9]{10,}",
        "gho_[A-Za-z0-9]{10,}",
        "xox[bpars]-[A-Za-z0-9-]{10,}",
        "AKIA[0-9A-Z]{16}",
        "AIza[0-9A-Za-z_-]{20,}",
    ],
    "privacy_keywords": [
        "密码",
        "password",
        "api key",
        "apikey",
        "api_key",
        "token",
        "secret",
        "cookie",
        "银行卡",
        "卡号",
        "验证码",
    ],
    # dedicated extraction LLM; defaults to following the chat agent's LLM
    "llm": {
        # "chat"   = reuse the conversation agent's LLM (default)
        # "custom" = dedicated OpenAI-compatible endpoint below
        "mode": "chat",
        "base_url": "",
        "api_key": "",
        "model": "",
    },
    # debug
    "memory_debug": True,
    # Phase 4 experience capture (deterministic interaction records; see
    # the experience/ package — capture hook + storage ride this switch)
    "experience": {
        "enabled": True,
    },
    # Phase 5 reflection (fact-only observations over experiences; see
    # the reflection/ package — offline/batch analysis, never in the
    # chat path)
    "reflection": {
        "enabled": True,
        "llm_analysis": True,   # LLM refinement toggle (needs an attached LLM)
        "batch_size": 20,       # max experiences per LLM call
        "llm_timeout": 60.0,
    },
    # storage backend: "sqlite" (default) | "hermes" (REST adapter over
    # the ai-companion Hermes Memory API; see storage/provider_factory.py)
    "storage": {
        "provider": "sqlite",
        "hermes": {
            "base_url": "http://127.0.0.1:12396",
            "user_id": "vtuber",
            "timeout": 10.0,
            # 构造时探活 /api/health, 后端不可用立即显式失败(不静默降级);
            # 测试 mock 场景可置 false
            "verify_on_start": True,
            # api_key 从环境变量 LTM_HERMES_API_KEY 读取, 不落盘
        },
    },
}

_managers: Dict[str, "MemoryManager"] = {}
_manager_lock = asyncio.Lock() if asyncio else None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# dedicated extraction LLM (config: llm.mode = "chat" | "custom")
# ---------------------------------------------------------------------------

_dedicated_llm = None
_dedicated_llm_stamp: Optional[str] = None


def _resolve_chat_llm_config() -> Optional[Dict[str, str]]:
    """Read the chat agent's LLM settings from conf.yaml.

    Returns the provider kwargs dict (model/base_url/llm_api_key) or None
    when conf.yaml is unreadable. Kept tolerant: any parse failure just
    falls back to reusing whatever live instance the agent passes in.
    """
    try:
        import yaml

        conf_path = os.path.join(os.getcwd(), "conf.yaml")
        with open(conf_path, "r", encoding="utf-8") as f:
            conf = yaml.safe_load(f)
        agent_cfg = (conf.get("character_config") or {}).get("agent_config") or {}
        settings = agent_cfg.get("agent_settings") or {}
        choice = agent_cfg.get("conversation_agent_choice")
        agent_block = settings.get(choice) or {}
        provider = agent_block.get("llm_provider")
        if not provider:
            return None
        llm_cfgs = agent_cfg.get("llm_configs") or {}
        prov_cfg = llm_cfgs.get(provider) or {}
        if not prov_cfg.get("model"):
            return None
        return {
            "provider": "openai_compatible_llm",
            "model": str(prov_cfg.get("model", "")),
            "base_url": str(prov_cfg.get("base_url", "") or ""),
            "llm_api_key": str(prov_cfg.get("llm_api_key", "") or ""),
        }
    except Exception as e:
        logger.debug(f"[LTM] chat llm config resolve failed: {e}")
        return None


def build_dedicated_llm(force: bool = False):
    """Create the extraction-only LLM instance from the llm config block.

    mode="chat"   -> mirror the chat agent's conf.yaml settings
    mode="custom" -> user-supplied base_url / api_key / model
    Returns the instance, or None in chat mode when conf.yaml has no
    usable LLM (the live agent instance remains the fallback then).
    """
    global _dedicated_llm, _dedicated_llm_stamp
    cfg = get_config().get("llm") or {}
    mode = str(cfg.get("mode", "chat")).lower()
    if mode == "chat":
        resolved = _resolve_chat_llm_config()
        if resolved is None:
            return None
        stamp = f"chat::{resolved['base_url']}::{resolved['model']}"
    else:  # custom
        if not (cfg.get("model") and cfg.get("base_url")):
            return None
        stamp = f"custom::{cfg.get('base_url')}::{cfg.get('model')}::{cfg.get('api_key', '')}"
    if not force and _dedicated_llm is not None and _dedicated_llm_stamp == stamp:
        return _dedicated_llm
    try:
        from src.open_llm_vtuber.agent.stateless_llm_factory import LLMFactory

        if mode == "chat":
            kwargs = resolved
        else:
            kwargs = {
                "provider": "openai_compatible_llm",
                "model": cfg.get("model"),
                "base_url": cfg.get("base_url"),
                "llm_api_key": cfg.get("api_key", "") or "EMPTY",
            }
        inst = LLMFactory.create_llm(
            kwargs.pop("provider"), **kwargs
        )
        _dedicated_llm = inst
        _dedicated_llm_stamp = stamp
        logger.info(
            f"[LTM] dedicated extraction LLM ready ({mode}: "
            f"{kwargs.get('model')} @ {kwargs.get('base_url')})"
        )
        return inst
    except Exception as e:
        logger.warning(f"[LTM] dedicated LLM build failed: {e}")
        return None


def _effective_extraction_llm(live_llm):
    """Pick the LLM used for extraction: dedicated config wins, else the
    live agent instance passed in from the conversation loop."""
    cfg = get_config().get("llm") or {}
    mode = str(cfg.get("mode", "chat")).lower()
    if mode == "custom":
        inst = build_dedicated_llm()
        if inst is not None:
            return inst
    return live_llm


def get_config() -> Dict[str, Any]:
    """Load module config, merged over defaults. Invalid file -> defaults."""
    cfg = dict(_DEFAULT_CONFIG)
    try:
        if os.path.exists(_CONFIG_PATH):
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            if isinstance(user_cfg, dict):
                cfg.update({k: v for k, v in user_cfg.items() if k in _DEFAULT_CONFIG})
                # llm is a nested block: merge subkeys over defaults so a
                # partial user block (e.g. only "mode") doesn't drop siblings
                if isinstance(user_cfg.get("llm"), dict):
                    merged = dict(cfg["llm"])
                    merged.update(
                        {k: v for k, v in user_cfg["llm"].items() if k in cfg["llm"]}
                    )
                    cfg["llm"] = merged
    except Exception as e:
        logger.warning(f"[LTM] failed to load memory_config.json: {e}")
    return cfg


def save_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Persist config changes to the module's own JSON file."""
    current = get_config()
    current.update({k: v for k, v in updates.items() if k in _DEFAULT_CONFIG})
    tmp = _CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _CONFIG_PATH)
    return current


async def get_manager(conf_uid: str, llm=None) -> Optional["MemoryManager"]:
    """Get (or create) the MemoryManager for a conf_uid.

    llm: optional stateless LLM instance reused from the live agent so the
    extractor stays provider-agnostic. Managers created earlier without an
    LLM get upgraded once one becomes available. A dedicated extraction
    LLM (llm.mode=custom) takes precedence over the live agent instance.
    """
    global _managers
    if not conf_uid:
        return None
    try:
        llm = _effective_extraction_llm(llm)
        mgr = _managers.get(conf_uid)
        if mgr is not None:
            if llm is not None:
                mgr.attach_llm(llm)
            return mgr
        if _manager_lock is None:
            return None
        async with _manager_lock:
            if conf_uid in _managers:
                mgr = _managers[conf_uid]
                if llm is not None:
                    mgr.attach_llm(llm)
                return mgr
            mgr = MemoryManager(conf_uid=conf_uid, config=get_config(), llm=llm)
            _managers[conf_uid] = mgr
            logger.info(f"[LTM] manager initialized for conf_uid={conf_uid}")
            return mgr
    except Exception as e:
        logger.error(f"[LTM] get_manager failed for {conf_uid}: {e}")
        return None


def get_manager_sync(conf_uid: str):
    """Synchronous peek used by the management API (never creates)."""
    return _managers.get(conf_uid)


def list_managers() -> List[str]:
    return list(_managers.keys())


def list_conf_uids() -> List[str]:
    """All conf_uids known to the system: live managers + DB files on disk.

    Lets the management panel populate its character selector even before
    any chat has happened since the last server restart.
    """
    uids = set(_managers.keys())
    try:
        data_dir = os.path.join(os.getcwd(), "long_term_memory_data")
        if os.path.isdir(data_dir):
            for fn in os.listdir(data_dir):
                if fn.endswith(".db"):
                    uids.add(fn[:-3])
    except Exception as e:
        logger.debug(f"[LTM] list_conf_uids scan failed: {e}")
    return sorted(uids)


def build_retrieval_context(
    conf_uid: str, user_text: str, llm=None
) -> Optional[Dict[str, Any]]:
    """Sync retrieval for the pre-chat hook. Returns a small dict placed
    into batch_input.metadata["ltm_context"], or None on any failure.

    Deliberately synchronous and keyword-based: must stay low-latency
    (spec: retrieval must not add perceivable delay to chat).
    """
    try:
        if not conf_uid or not user_text or not isinstance(user_text, str):
            return None
        cfg = get_config()
        if not cfg.get("enabled", True):
            return None
        mgr = _managers.get(conf_uid)
        if mgr is None:
            # create manager without LLM (LLM only needed for extraction;
            # if this is the first turn, extraction upgrade happens later)
            if _manager_lock is None:
                return None
            try:
                mgr = MemoryManager(
                    conf_uid=conf_uid, config=cfg, llm=_effective_extraction_llm(llm)
                )
                _managers[conf_uid] = mgr
            except Exception as e:
                logger.warning(f"[LTM] lazy manager creation failed: {e}")
                return None
        return mgr.retrieve_for_prompt(user_text)
    except Exception as e:
        logger.warning(f"[LTM] retrieval context build failed: {e}")
        return None


async def extract_from_turn(
    conf_uid: str,
    user_text: str,
    ai_text: str,
    llm=None,
    history_snapshot: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Post-reply async extraction entry (fire-and-forget task target).

    Never raises: any failure is logged and swallowed so the chat loop
    is unaffected (spec: graceful degradation).
    """
    try:
        # get_manager internally resolves the dedicated-vs-live LLM choice
        mgr = await get_manager(conf_uid, llm=llm)
        if mgr is None:
            return
        await mgr.extract_memories(
            user_text=user_text,
            ai_text=ai_text,
            history_snapshot=history_snapshot,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"[LTM] extract_from_turn failed: {e}")


__all__ = [
    "get_manager",
    "get_manager_sync",
    "list_managers",
    "list_conf_uids",
    "build_retrieval_context",
    "extract_from_turn",
    "get_config",
    "save_config",
    "MemoryManager",
    "MemoryStore",
]
