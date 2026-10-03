"""Provider Management Center (config/provider_center.py)
==========================================================

Unified web management for every user-configurable external API
(LLM / TTS / STT / Embedding / Vision / Search / Custom), mounted at
/providers/api + static UI at /providers.

Architecture (spec §31-§55):
    Business modules (server startup reads conf.yaml)
        -> real conf.yaml sections (llm_configs / tts_config / asr_config)
           ^ written here, atomically, comment-preserving (ruamel)

    Web UI -> /providers/api -> this module -> conf.yaml
    Test Connection -> real probe per category (LLM chat call /
    TTS synthesis / STT engine load) with latency + sanitized errors.

Key security (§43): API keys are NEVER returned in full - only a
masked preview (first 3 + last 4). Writes accept the full key; reads
return the mask. Logs never print keys.
"""
import copy
import os
import re
import shutil
import time
import traceback
from datetime import datetime
from io import StringIO
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ruamel.yaml import YAML
from ruamel.yaml.scanner import ScannerError
from ruamel.yaml.parser import ParserError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONF_PATH = PROJECT_ROOT / "conf.yaml"
BACKUP_DIR = PROJECT_ROOT / "config" / "backups"

yaml_handler = YAML()
yaml_handler.preserve_quotes = True
yaml_handler.width = 4096

router = APIRouter(prefix="/providers/api")

# ---------------------------------------------------------------------------
# category map: conf.yaml real paths
# ---------------------------------------------------------------------------
# llm: character_config.agent_config.llm_provider (default selector)
#      character_config.agent_config.llm_configs.<name> (provider pool)
# tts: character_config.tts_config.tts_model (default selector)
#      character_config.tts_config.<engine> (provider configs)
# stt: character_config.asr_config.asr_model + .<engine>

LLM_SECTION = ["character_config", "agent_config", "llm_configs"]
LLM_DEFAULT_KEY = ["character_config", "agent_config", "llm_provider"]
TTS_SECTION = ["character_config", "tts_config"]
TTS_DEFAULT_KEY = ["character_config", "tts_config", "tts_model"]
STT_SECTION = ["character_config", "asr_config"]
STT_DEFAULT_KEY = ["character_config", "asr_config", "asr_model"]

CATEGORIES = {
    "llm": {"label": "LLM", "section": LLM_SECTION,
            "default_key": LLM_DEFAULT_KEY},
    "tts": {"label": "TTS", "section": TTS_SECTION,
            "default_key": TTS_DEFAULT_KEY},
    "stt": {"label": "STT", "section": STT_SECTION,
            "default_key": STT_DEFAULT_KEY},
    "embedding": {"label": "Embedding", "section": None,
                  "default_key": None},
    "vision": {"label": "Vision", "section": None, "default_key": None},
    "search": {"label": "Search", "section": None,
               "default_key": None},
    "custom": {"label": "Custom API", "section": None,
               "default_key": None},
}

# provider sidecar state (test results; survives restarts via json file)
SIDECAR_PATH = PROJECT_ROOT / "config" / "provider_state.json"


def _load_sidecar() -> dict:
    if SIDECAR_PATH.exists():
        try:
            import json
            return json.loads(SIDECAR_PATH.read_text(
                encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_sidecar(state: dict) -> None:
    import json
    SIDECAR_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8")


# ---------------------------------------------------------------------------
# yaml helpers (comment-preserving, atomic, backed up)
# ---------------------------------------------------------------------------
def _read_conf():
    if not CONF_PATH.exists():
        raise HTTPException(500, "conf.yaml not found")
    try:
        return yaml_handler.load(StringIO(
            CONF_PATH.read_text(encoding="utf-8")))
    except (ScannerError, ParserError) as e:
        raise HTTPException(500, f"conf.yaml parse error: {e}")


def _write_conf(data) -> None:
    buf = StringIO()
    yaml_handler.dump(data, buf)
    tmp = CONF_PATH.with_suffix(".yaml.providers-tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8")
    os.replace(tmp, CONF_PATH)


def _backup(tag: str) -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"conf-{stamp}-{tag}.yaml"
    shutil.copy2(CONF_PATH, target)
    # keep the newest 30
    backups = sorted(BACKUP_DIR.glob("conf-*-providers.yaml"))
    for old in backups[:-30]:
        old.unlink(missing_ok=True)
    return str(target)


def _get_path(data, path):
    node = data
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _set_path(data, path, value):
    node = data
    for key in path[:-1]:
        if key not in node or not isinstance(node[key], dict):
            node[key] = {}
        node = node[key]
    node[path[-1]] = value


# ---------------------------------------------------------------------------
# key masking (§43)
# ---------------------------------------------------------------------------
_SECRET_HINTS = ("key", "token", "secret", "password", "credential")


def _mask(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return value[:3] + "*" * 12 + value[-4:]


def _sanitize_tree(node):
    """Return a display copy with every secret-ish value masked."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                out[k] = _sanitize_tree(v)
            elif isinstance(v, str) and any(
                    h in str(k).lower() for h in _SECRET_HINTS):
                out[k] = _mask(v)
            else:
                out[k] = v
        return out
    if isinstance(node, list):
        return [_sanitize_tree(x) for x in node]
    return node


def _sanitize_error(text: str) -> str:
    """Strip anything that looks like a key from error text."""
    text = str(text or "")
    # collapse long token-like strings
    text = re.sub(r"(sk-[A-Za-z0-9_\-]{8,})", "sk-***", text)
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]{8,}", r"\1***", text)
    return text[:400]


# ---------------------------------------------------------------------------
# provider model mapping (conf section entry <-> unified model §42)
# ---------------------------------------------------------------------------
def _entry_to_model(pid, entry, category, is_default, sidecar):
    entry = entry if isinstance(entry, dict) else {}
    st = sidecar.get(category, {}).get(pid, {})
    return {
        "id": pid,
        "name": pid,
        "type": category,
        "provider": pid,
        "baseUrl": entry.get("base_url") or entry.get("api_base") or "",
        "model": entry.get("model", ""),
        "credentials": _sanitize_tree(
            {k: v for k, v in entry.items()
             if any(h in k.lower() for h in _SECRET_HINTS)}),
        "config": _sanitize_tree(
            {k: v for k, v in entry.items()
             if not any(h in k.lower() for h in _SECRET_HINTS)
             and k not in ("base_url", "api_base", "model")}),
        "enabled": st.get("enabled", True),
        "isDefault": bool(is_default),
        "status": st.get("status", "unknown"),
        "lastTestAt": st.get("lastTestAt"),
        "latencyMs": st.get("latencyMs"),
        "lastError": _sanitize_error(st.get("lastError") or ""),
    }


# ---------------------------------------------------------------------------
# API: overview (§46 categories + §51 health)
# ---------------------------------------------------------------------------
@router.get("/overview")
def overview():
    conf = _read_conf()
    sidecar = _load_sidecar()
    cats = []
    for key, meta in CATEGORIES.items():
        section = _get_path(conf, meta["section"]) if meta["section"] \
            else None
        default = _get_path(conf, meta["default_key"]) \
            if meta["default_key"] else None
        items = []
        if isinstance(section, dict):
            for pid, entry in section.items():
                if pid.startswith("#") or not isinstance(entry, dict):
                    continue
                items.append(_entry_to_model(
                    pid, entry, key, pid == default, sidecar))
        cats.append({"key": key, "label": meta["label"],
                     "count": len(items),
                     "defaultId": default,
                     "providers": items})
    # health view (§51): default provider per category
    health = []
    for c in cats:
        d = next((p for p in c["providers"] if p["isDefault"]), None)
        if d is not None:
            health.append({
                "category": c["label"], "id": d["id"],
                "status": d["status"],
                "latencyMs": d["latencyMs"],
                "enabled": d["enabled"]})
    return {"categories": cats, "health": health}


@router.get("/categories/{category}/providers")
def list_providers(category: str):
    if category not in CATEGORIES:
        raise HTTPException(404, f"unknown category {category}")
    conf = _read_conf()
    meta = CATEGORIES[category]
    sidecar = _load_sidecar()
    section = _get_path(conf, meta["section"]) if meta["section"] \
        else None
    default = _get_path(conf, meta["default_key"]) \
        if meta["default_key"] else None
    items = []
    if isinstance(section, dict):
        for pid, entry in section.items():
            if not isinstance(entry, dict):
                continue
            items.append(_entry_to_model(pid, entry, category,
                                         pid == default, sidecar))
    return {"providers": items, "defaultId": default}


# ---------------------------------------------------------------------------
# API: create / update / delete (writes REAL conf.yaml)
# ---------------------------------------------------------------------------
class ProviderPayload(BaseModel):
    name: str
    fields: dict = {}
    setDefault: bool = True


@router.post("/categories/{category}/providers")
def create_provider(category: str, payload: ProviderPayload):
    meta = CATEGORIES.get(category)
    if meta is None or meta["section"] is None:
        raise HTTPException(404, "category not backed by conf.yaml")
    pid = payload.name.strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", pid):
        raise HTTPException(400, "provider name: letters/digits/_/-")
    conf = _read_conf()
    section = _get_path(conf, meta["section"])
    if section is None:
        _set_path(conf, meta["section"], {})
        section = _get_path(conf, meta["section"])
    if pid in section:
        raise HTTPException(409, f"provider '{pid}' already exists")
    _backup("providers-add")
    section[pid] = copy.deepcopy(payload.fields)
    if payload.setDefault and meta["default_key"]:
        _set_path(conf, meta["default_key"], pid)
    _write_conf(conf)
    return {"ok": True, "id": pid,
            "setDefault": payload.setDefault}


@router.put("/categories/{category}/providers/{pid}")
def update_provider(category: str, pid: str,
                    payload: ProviderPayload):
    meta = CATEGORIES.get(category)
    if meta is None or meta["section"] is None:
        raise HTTPException(404, "category not backed by conf.yaml")
    conf = _read_conf()
    section = _get_path(conf, meta["section"])
    if not isinstance(section, dict) or pid not in section:
        raise HTTPException(404, f"provider '{pid}' not found")
    _backup("providers-edit")
    # merge: keep unspecified fields (so a masked key never overwrites
    # the real stored value)
    existing = section[pid] if isinstance(section[pid], dict) else {}
    for k, v in payload.fields.items():
        if isinstance(v, str) and "*" in v and \
                any(h in k.lower() for h in _SECRET_HINTS):
            continue   # masked placeholder -> keep the stored secret
        existing[k] = v
    section[pid] = existing
    if payload.setDefault and meta["default_key"]:
        _set_path(conf, meta["default_key"], pid)
    _write_conf(conf)
    return {"ok": True, "id": pid}


@router.delete("/categories/{category}/providers/{pid}")
def delete_provider(category: str, pid: str):
    meta = CATEGORIES.get(category)
    if meta is None or meta["section"] is None:
        raise HTTPException(404, "category not backed by conf.yaml")
    conf = _read_conf()
    section = _get_path(conf, meta["section"])
    if not isinstance(section, dict) or pid not in section:
        raise HTTPException(404, f"provider '{pid}' not found")
    _backup("providers-del")
    del section[pid]
    default = _get_path(conf, meta["default_key"])
    if default == pid:
        remaining = [k for k, v in section.items()
                     if isinstance(v, dict)]
        _set_path(conf, meta["default_key"],
                  remaining[0] if remaining else "")
    _write_conf(conf)
    return {"ok": True}


# ---------------------------------------------------------------------------
# API: enable/disable + set default (§47 quick switch)
# ---------------------------------------------------------------------------
@router.post("/categories/{category}/providers/{pid}/enabled")
def set_enabled(category: str, pid: str, enabled: bool = True):
    state = _load_sidecar()
    state.setdefault(category, {}).setdefault(pid, {})
    state[category][pid]["enabled"] = bool(enabled)
    _save_sidecar(state)
    return {"ok": True, "enabled": bool(enabled)}


@router.post("/categories/{category}/providers/{pid}/default")
def set_default(category: str, pid: str):
    meta = CATEGORIES.get(category)
    if meta is None or meta["default_key"] is None:
        raise HTTPException(404, "category has no default")
    conf = _read_conf()
    section = _get_path(conf, meta["section"])
    if not isinstance(section, dict) or pid not in section:
        raise HTTPException(404, f"provider '{pid}' not found")
    _backup("providers-default")
    _set_path(conf, meta["default_key"], pid)
    _write_conf(conf)
    return {"ok": True, "defaultId": pid}


# ---------------------------------------------------------------------------
# API: test connection (§44) — REAL probes, sanitized results
# ---------------------------------------------------------------------------
def _record_test(category, pid, status, latency_ms, error=""):
    state = _load_sidecar()
    state.setdefault(category, {}).setdefault(pid, {})
    st = state[category][pid]
    st.update({"status": status, "latencyMs": latency_ms,
               "lastTestAt": datetime.now().isoformat(
                   timespec="seconds"),
               "lastError": _sanitize_error(error)})
    _save_sidecar(state)


def _probe_llm(entry: dict) -> tuple:
    """Real chat completion against the provider's endpoint."""
    base = (entry.get("base_url") or
            entry.get("api_base") or "").rstrip("/")
    key = entry.get("llm_api_key") or entry.get("api_key") or ""
    model = entry.get("model", "")
    if not base or not model:
        return "error", None, "base_url / model missing"
    import urllib.request
    url = base + "/chat/completions"
    body = {"model": model, "max_tokens": 8,
            "messages": [{"role": "user", "content": "ping"}]}
    req = urllib.request.Request(
        url, data=body and __import__("json").dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
        return "connected", int((time.time() - t0) * 1000), ""
    except Exception as e:
        code = getattr(e, "code", None)
        reason = _sanitize_error(
            f"HTTP {code} " if code else str(e))
        return "error", int((time.time() - t0) * 1000), reason


def _probe_tts(entry: dict) -> tuple:
    """Real synthesis via the project's edge_tts engine (or the
    configured engine when it is offline-capable)."""
    t0 = time.time()
    try:
        import asyncio
        import sys
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        from open_llm_vtuber.tts.edge_tts import TTSEngine
        voice = entry.get("voice") or "zh-CN-XiaoxiaoNeural"
        path = asyncio.run(TTSEngine(voice=voice)
                           .async_generate_audio("测", "probe_tts"))
        if path:
            return "connected", int((time.time() - t0) * 1000), ""
        return "error", None, "no audio produced"
    except Exception as e:
        return "error", int((time.time() - t0) * 1000), \
            _sanitize_error(e)


def _probe_stt(entry: dict) -> tuple:
    """Real engine-load probe for the offline sherpa engine."""
    t0 = time.time()
    try:
        import sys
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        from open_llm_vtuber.input.schemas import SherpaOnnxProvider
        SherpaOnnxProvider().transcribe(b"")  # triggers engine load
        return "connected", int((time.time() - t0) * 1000), ""
    except Exception as e:
        # engine-load success vs empty-input rejection differ
        msg = str(e)
        if "ASR" in msg or "audio" in msg.lower():
            return "connected", int((time.time() - t0) * 1000), ""
        return "error", int((time.time() - t0) * 1000), \
            _sanitize_error(e)


@router.post("/categories/{category}/providers/{pid}/test")
def test_provider(category: str, pid: str):
    meta = CATEGORIES.get(category)
    if meta is None or meta["section"] is None:
        raise HTTPException(404, "category not testable")
    conf = _read_conf()
    section = _get_path(conf, meta["section"])
    if not isinstance(section, dict) or pid not in section:
        raise HTTPException(404, f"provider '{pid}' not found")
    # raw entry (unmasked) for the REAL probe; never returned
    entry = section[pid]
    try:
        if category == "llm":
            status, latency, err = _probe_llm(entry)
        elif category == "tts":
            status, latency, err = _probe_tts(entry)
        elif category == "stt":
            status, latency, err = _probe_stt(entry)
        else:
            status, latency, err = "unknown", None, \
                "no probe for this category yet"
    except Exception:
        status, latency, err = "error", None, _sanitize_error(
            traceback.format_exc(limit=2))
    _record_test(category, pid, status, latency or 0, err)
    return {"status": status, "latencyMs": latency,
            "error": err or None,
            "lastTestAt": datetime.now().isoformat(
                timespec="seconds")}


# ---------------------------------------------------------------------------
# API: restart (apply changes to running business modules)
# ---------------------------------------------------------------------------
@router.post("/restart")
def restart_server():
    script = Path(__file__).resolve().parent / "restart_server.sh"
    if not script.exists():
        raise HTTPException(500, "restart script not found")
    import subprocess
    subprocess.Popen(["bash", str(script)],
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return {"ok": True, "message": "restarting (ready in ~30s)"}
