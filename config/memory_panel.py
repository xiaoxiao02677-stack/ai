"""
Memory Panel Router (config/memory_panel.py)
===============================================

Web management panel for the long-term memory system, served under /memory on
the public HTTPS port 12393 (TLS proxy -> internal 127.0.0.1:12395). Mounted
from src/open_llm_vtuber/server.py before the frontend catch-all — same
pattern as config/panel.py.

The same routes are ALSO registered under /api/memory to satisfy the spec's
literal REST paths (GET/POST/PUT/DELETE /api/memory, /api/memory/keywords,
/api/memory/state, debug endpoint).

Routes (relative, on both prefixes):
    GET    /sessions                -> known conf_uids (managers + DB files)
    GET    /memories                -> list (query: conf_uid, status, type, q)
    POST   /memories                -> manual add (privacy-checked)
    PUT    /memories/{memory_id}    -> edit content/type/keywords/scores/status
    DELETE /memories/{memory_id}    -> delete (query: conf_uid)
    GET    /keywords                -> keyword list (query: conf_uid)
    POST   /keywords                -> manual add
    DELETE /keywords                -> delete (query: conf_uid, keyword, category)
    GET    /state                   -> user state + summary + turn count
    PUT    /state                   -> update user state fields
    GET    /debug                   -> stats + live config + manager status
    POST   /test-retrieve           -> run retrieval pipeline on sample text
    GET    /config                  -> memory module config
    POST   /config                  -> update config (whitelisted keys)
    GET    /llm-info                -> chat agent's LLM settings (key masked)
    POST   /llm-test                -> live connectivity probe for the
                                       extraction LLM (chat or custom form)
    POST   /wipe                    -> delete EVERYTHING for a conf_uid

Frontend static files live in config/memory_web/ (index.html, style.css,
app.js), served at /memory/ via StaticFiles(html=True).
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from loguru import logger

try:
    from src.open_llm_vtuber import long_term_memory as ltm
except ImportError:  # direct in-package import fallback
    from open_llm_vtuber import long_term_memory as ltm

try:
    from src.open_llm_vtuber.long_term_memory.schemas import (
        MEMORY_TYPES,
        KEYWORD_CATEGORIES,
        KeywordRecord,
        MemoryRecord,
        UserState,
    )
    from src.open_llm_vtuber.long_term_memory.privacy import (
        privacy_check,
        sanitize_memory_text,
    )
except ImportError:  # direct in-package import fallback
    from open_llm_vtuber.long_term_memory.schemas import (
        MEMORY_TYPES,
        KEYWORD_CATEGORIES,
        KeywordRecord,
        MemoryRecord,
        UserState,
    )
    from open_llm_vtuber.long_term_memory.privacy import (
        privacy_check,
        sanitize_memory_text,
    )


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------

class MemoryCreate(BaseModel):
    conf_uid: str
    memory_type: str
    content: str
    keywords: Optional[List[str]] = None
    importance: float = 0.6
    confidence: float = 0.9


class MemoryUpdate(BaseModel):
    conf_uid: str
    memory_type: Optional[str] = None
    content: Optional[str] = None
    keywords: Optional[List[str]] = None
    importance: Optional[float] = None
    confidence: Optional[float] = None
    status: Optional[str] = None


class KeywordCreate(BaseModel):
    conf_uid: str
    keyword: str
    category: str = "topic"


class StateUpdate(BaseModel):
    conf_uid: str
    emotion: Optional[str] = None
    energy: Optional[float] = None
    stress: Optional[float] = None
    current_topic: Optional[str] = None
    intent: Optional[str] = None


class RetrieveTest(BaseModel):
    conf_uid: str
    text: str


class ConfigUpdate(BaseModel):
    updates: Dict[str, Any]


class LLMTestRequest(BaseModel):
    # test the form values before saving: mode/base_url/api_key/model
    mode: str = "chat"
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class WipeRequest(BaseModel):
    conf_uid: str
    confirm: str


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def _get_manager(conf_uid: str):
    """Manager for a conf_uid, creating one (without LLM) if needed so the
    panel works against on-disk DBs even before any chat since restart."""
    if not conf_uid:
        raise HTTPException(400, "conf_uid is required")
    mgr = await ltm.get_manager(conf_uid)
    if mgr is None:
        raise HTTPException(500, f"failed to init memory manager for {conf_uid}")
    return mgr


def _validate_type(memory_type: str) -> str:
    if memory_type not in MEMORY_TYPES:
        raise HTTPException(400, f"invalid memory_type, must be one of {MEMORY_TYPES}")
    return memory_type


def _validate_content(content: str) -> str:
    cleaned = sanitize_memory_text(content)
    if not cleaned or len(cleaned) < 2:
        raise HTTPException(400, "content too short after sanitization")
    check = privacy_check(cleaned)
    if not check["ok"]:
        raise HTTPException(400, f"content rejected by privacy filter: {check['reason']}")
    return cleaned


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


# ---------------------------------------------------------------------------
# routes (registered on both /memory/api and /api/memory)
# ---------------------------------------------------------------------------

routes = APIRouter()


@routes.get("/sessions")
async def get_sessions():
    return {
        "conf_uids": ltm.list_conf_uids(),
        "live_managers": ltm.list_managers(),
        "enabled": bool(ltm.get_config().get("enabled", True)),
    }


@routes.get("/memories")
async def list_memories(
    conf_uid: str = Query(...),
    status: str = Query("active"),
    memory_type: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
):
    mgr = await _get_manager(conf_uid)
    if status == "all":
        records = mgr.store.list_all_memories()
    else:
        if status not in ("active", "deprecated"):
            raise HTTPException(400, "status must be active|deprecated|all")
        records = mgr.store.list_memories(status=status)
    if memory_type:
        records = [r for r in records if r.memory_type == memory_type]
    if q:
        ql = q.lower()
        records = [
            r
            for r in records
            if ql in r.content.lower() or any(ql in k.lower() for k in r.keywords)
        ]
    return {"memories": [r.to_dict() for r in records], "total": len(records)}


@routes.post("/memories")
async def create_memory(req: MemoryCreate):
    mgr = await _get_manager(req.conf_uid)
    memory_type = _validate_type(req.memory_type)
    content = _validate_content(req.content)
    rec = MemoryRecord.new(
        conf_uid=req.conf_uid,
        memory_type=memory_type,
        content=content,
        keywords=[k.strip() for k in (req.keywords or []) if k.strip()],
        importance=_clamp(req.importance),
        confidence=_clamp(req.confidence),
        source_history_uid="manual",
    )
    mgr.store.add_memory(rec)
    logger.info(f"[LTM-panel] manual memory added: {content[:40]}")
    return {"ok": True, "memory": rec.to_dict()}


@routes.put("/memories/{memory_id}")
async def update_memory(memory_id: str, req: MemoryUpdate):
    mgr = await _get_manager(req.conf_uid)
    rec = mgr.store.get_memory(memory_id)
    if rec is None or rec.conf_uid != req.conf_uid:
        raise HTTPException(404, f"memory {memory_id} not found for {req.conf_uid}")
    if req.memory_type is not None:
        rec.memory_type = _validate_type(req.memory_type)
    if req.content is not None:
        rec.content = _validate_content(req.content)
    if req.keywords is not None:
        rec.keywords = [k.strip() for k in req.keywords if k.strip()]
    if req.importance is not None:
        rec.importance = _clamp(req.importance)
    if req.confidence is not None:
        rec.confidence = _clamp(req.confidence)
    if req.status is not None:
        if req.status not in ("active", "deprecated"):
            raise HTTPException(400, "status must be active|deprecated")
        rec.status = req.status
    mgr.store.update_memory(rec)
    return {"ok": True, "memory": rec.to_dict()}


@routes.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str, conf_uid: str = Query(...)):
    mgr = await _get_manager(conf_uid)
    if not mgr.store.delete_memory(memory_id):
        raise HTTPException(404, f"memory {memory_id} not found")
    return {"ok": True}


@routes.get("/keywords")
async def list_keywords(conf_uid: str = Query(...)):
    mgr = await _get_manager(conf_uid)
    return {"keywords": [k.to_dict() for k in mgr.store.list_keywords(limit=500)]}


@routes.post("/keywords")
async def create_keyword(req: KeywordCreate):
    mgr = await _get_manager(req.conf_uid)
    kw = req.keyword.strip()
    if not kw:
        raise HTTPException(400, "keyword must not be empty")
    category = req.category if req.category in KEYWORD_CATEGORIES else "topic"
    mgr.store.upsert_keyword(kw, category)
    return {"ok": True, "keyword": kw, "category": category}


@routes.delete("/keywords")
async def delete_keyword(
    conf_uid: str = Query(...), keyword: str = Query(...), category: str = Query(...)
):
    mgr = await _get_manager(conf_uid)
    if not mgr.store.delete_keyword(keyword, category):
        raise HTTPException(404, f"keyword '{keyword}' ({category}) not found")
    return {"ok": True}


@routes.get("/state")
async def get_state(conf_uid: str = Query(...)):
    mgr = await _get_manager(conf_uid)
    state = mgr.store.get_state()
    return {
        "state": state.to_dict() if state else None,
        "summary": mgr.store.get_summary(),
        "turn_count": mgr.store.get_turn_count(),
    }


@routes.put("/state")
async def update_state(req: StateUpdate):
    mgr = await _get_manager(req.conf_uid)
    state = mgr.store.get_state() or UserState(conf_uid=req.conf_uid)
    if req.emotion is not None:
        state.emotion = str(req.emotion).strip()[:30] or "neutral"
    if req.energy is not None:
        state.energy = _clamp(req.energy)
    if req.stress is not None:
        state.stress = _clamp(req.stress)
    if req.current_topic is not None:
        state.current_topic = str(req.current_topic).strip()[:60]
    if req.intent is not None:
        state.intent = str(req.intent).strip()[:30] or "chat"
    mgr.store.save_state(state)
    return {"ok": True, "state": state.to_dict()}


@routes.get("/debug")
async def get_debug(conf_uid: str = Query(...)):
    mgr = await _get_manager(conf_uid)
    return mgr.as_debug_dict()


@routes.post("/test-retrieve")
async def test_retrieve(req: RetrieveTest):
    """Run the full retrieval pipeline on sample text without touching chat."""
    if not req.text.strip():
        raise HTTPException(400, "text must not be empty")
    mgr = await _get_manager(req.conf_uid)
    try:
        results = mgr.retriever.retrieve(req.text)
        return {
            "results": [
                {
                    "score": item["score"],
                    "components": item["components"],
                    "memory": item["record"].to_dict(),
                }
                for item in results
            ],
            "injection_preview": mgr.retrieve_for_prompt(req.text),
        }
    except Exception as e:
        raise HTTPException(500, f"retrieval failed: {e}")


@routes.get("/config")
async def get_memory_config():
    return ltm.get_config()


@routes.post("/config")
async def update_memory_config(req: ConfigUpdate):
    try:
        updated = ltm.save_config(req.updates)
        return {"ok": True, "config": updated}
    except Exception as e:
        raise HTTPException(500, f"save failed: {e}")


@routes.get("/llm-info")
async def get_llm_info():
    """What the extraction LLM would use right now, for the settings page.

    - chat mode: mirror of the conversation agent's conf.yaml LLM block
      (key masked); falls back to the live agent instance note
    - custom mode: the saved dedicated endpoint (key masked)
    """
    cfg = ltm.get_config().get("llm") or {}
    mode = str(cfg.get("mode", "chat")).lower()

    def mask(k: str) -> str:
        if not k:
            return ""
        return k[:6] + "…" + k[-4:] if len(k) > 12 else "****"

    info: Dict[str, Any] = {"mode": mode}
    if mode == "custom":
        info.update(
            {
                "base_url": cfg.get("base_url", ""),
                "model": cfg.get("model", ""),
                "api_key_masked": mask(str(cfg.get("api_key", ""))),
            }
        )
    else:
        resolved = ltm._resolve_chat_llm_config()
        if resolved:
            info.update(
                {
                    "base_url": resolved.get("base_url", ""),
                    "model": resolved.get("model", ""),
                    "api_key_masked": mask(resolved.get("llm_api_key", "")),
                    "source": "conf.yaml",
                }
            )
        else:
            # no usable conf.yaml LLM: extraction rides the live agent instance
            live = [
                uid
                for uid in ltm.list_managers()
                if (ltm.get_manager_sync(uid) is not None)
            ]
            info.update(
                {
                    "base_url": "",
                    "model": "",
                    "api_key_masked": "",
                    "source": "live-agent",
                    "live_managers": live,
                }
            )
    return info


@routes.post("/llm-test")
async def test_llm(req: LLMTestRequest):
    """Fire one minimal chat_completion at the extraction LLM endpoint.

    Tests the submitted form values (no save needed): chat mode resolves
    conf.yaml live, custom mode uses base_url/api_key/model as typed.
    Returns ok + latency + a reply preview, or a readable error.
    """
    import time

    mode = req.mode if req.mode in ("chat", "custom") else "chat"
    if mode == "custom" and not (req.base_url.strip() and req.model.strip()):
        raise HTTPException(400, "自定义模式需要填写 API 地址和模型名称")

    # build a one-off instance without touching the module cache
    try:
        from src.open_llm_vtuber.agent.stateless_llm_factory import LLMFactory
    except ImportError:
        from open_llm_vtuber.agent.stateless_llm_factory import LLMFactory

    if mode == "custom":
        inst = LLMFactory.create_llm(
            "openai_compatible_llm",
            model=req.model.strip(),
            base_url=req.base_url.strip(),
            llm_api_key=req.api_key.strip() or "EMPTY",
        )
        shown = {"mode": "custom", "base_url": req.base_url.strip(), "model": req.model.strip()}
    else:
        resolved = ltm._resolve_chat_llm_config()
        if resolved is None:
            raise HTTPException(
                400,
                "无法从 conf.yaml 解析聊天 LLM 配置（检查 conversation_agent_choice "
                "和对应的 llm_configs 条目）",
            )
        kwargs = dict(resolved)
        inst = LLMFactory.create_llm(kwargs.pop("provider"), **kwargs)
        shown = {
            "mode": "chat",
            "base_url": resolved.get("base_url", ""),
            "model": resolved.get("model", ""),
        }

    t0 = time.time()
    pieces: List[str] = []
    try:
        stream = inst.chat_completion(
            [{"role": "user", "content": "ping，请回复 pong"}], "你是连通性测试。"
        )
        async for event in stream:
            if isinstance(event, str):
                if event == "__API_NOT_SUPPORT_TOOLS__":
                    continue
                # the OpenAI-compatible adapter yields errors as plain text
                # chunks instead of raising — treat them as failures
                if event.startswith("Error calling the chat endpoint"):
                    raise RuntimeError(event.split("\n")[0])
                pieces.append(event)
            elif isinstance(event, dict):
                if event.get("type") == "text_delta":
                    pieces.append(event.get("text", ""))
                elif event.get("type") == "error":
                    raise RuntimeError(str(event.get("message", "LLM error event")))
    except Exception as e:
        raise HTTPException(502, f"LLM 调用失败：{str(e)[:300]}")
    latency_ms = int((time.time() - t0) * 1000)
    reply = "".join(pieces).strip()
    if not reply:
        raise HTTPException(502, "LLM 返回了空响应，请检查模型是否可用")
    return {
        "ok": True,
        "latency_ms": latency_ms,
        "reply_preview": reply[:80],
        **shown,
    }


@routes.post("/wipe")
async def wipe_all(req: WipeRequest):
    """Danger: delete all memories/keywords/state/summary for a conf_uid."""
    if req.confirm != "DELETE":
        raise HTTPException(400, "confirm must be the literal string DELETE")
    mgr = await _get_manager(req.conf_uid)
    n_mem = 0
    for rec in mgr.store.list_all_memories():
        mgr.store.delete_memory(rec.memory_id)
        n_mem += 1
    n_kw = 0
    for kw in mgr.store.list_keywords(limit=100000):
        mgr.store.delete_keyword(kw.keyword, kw.category)
        n_kw += 1
    mgr.store.save_state(UserState(conf_uid=req.conf_uid))
    mgr.store.save_summary("", 0)
    logger.warning(f"[LTM-panel] wiped {req.conf_uid}: {n_mem} memories, {n_kw} keywords")
    return {"ok": True, "deleted_memories": n_mem, "deleted_keywords": n_kw}


# ---------------------------------------------------------------------------
# dual registration: /memory/api (panel UI) + /api/memory (spec-literal)
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/memory/api")
router.include_router(routes)

api_alias = APIRouter(prefix="/api/memory")
api_alias.include_router(routes)
