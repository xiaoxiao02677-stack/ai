"""Control Center (config/control_center.py)
============================================

The single top-level console aggregating every management surface
(spec §53 skeleton). It does NOT re-implement the panels — it links
and aggregates them:

    Control Center (/cc)
    ├── Overview   (this module: live aggregates, real data only)
    ├── Providers  -> /providers  (provider_center)
    ├── Devices    -> /workshop   (workshop_panel)
    ├── Memory     -> /memory     (memory_panel)
    ├── Config     -> /config     (panel.py, conf.yaml editor)
    └── System     (this module: service status + restart)

Overview data is REAL: workshop device totals, provider health from
the sidecar, LTM memory counts from the SQLite stores, service
versions/ports from the live process. No fake UI numbers (§52).
"""
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
router = APIRouter(prefix="/cc/api")

_CONF_DIR = PROJECT_ROOT / "config"
_LTM_DIR = PROJECT_ROOT / "long_term_memory_data"
_CACHE_DIR = PROJECT_ROOT / "cache"
_LOGS_DIR = PROJECT_ROOT / "logs"
START = time.time()


# ---------------------------------------------------------------------------
# helpers: live status of the sibling services
# ---------------------------------------------------------------------------
def _http_ok(url: str, timeout: float = 2.0) -> tuple:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200, r.status
    except Exception as e:
        return False, getattr(e, "code", None) or str(e)[:60]


def _dir_stats(path: Path):
    if not path.exists():
        return {"exists": False, "files": 0, "size_mb": 0.0}
    total = 0
    count = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
                count += 1
            except OSError:
                pass
    return {"exists": True, "files": count,
            "size_mb": round(total / 1048576, 1)}


def _git_state():
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
            timeout=5).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
            timeout=5).stdout.strip()
        return {"commit": head or "?",
                "clean": not bool(dirty)}
    except Exception:
        return {"commit": "?", "clean": None}


# ---------------------------------------------------------------------------
# Overview: the aggregate dashboard (real data only)
# ---------------------------------------------------------------------------
@router.get("/overview")
def overview():
    # devices (via the live workshop API on the internal port)
    devices = {"online": 0, "stale": 0, "disconnected": 0, "total": 0,
               "ids": []}
    try:
        import urllib.request
        with urllib.request.urlopen(
                "http://127.0.0.1:12395/workshop/api/overview",
                timeout=4) as r:
            d = __import__("json").loads(r.read())
        devices = {
            "online": d["totals"]["online"],
            "stale": d["totals"]["stale"],
            "disconnected": d["totals"]["disconnected"],
            "total": d["totals"]["devices"],
            "ids": [x["device_id"] for x in d["devices"]][:8],
        }
    except Exception:
        pass   # workshop down -> zeros (honest)

    # provider health (from the provider center sidecar)
    providers = []
    try:
        import json
        sidecar = json.loads(
            (_CONF_DIR / "provider_state.json").read_text(
                encoding="utf-8")) \
            if (_CONF_DIR / "provider_state.json").exists() else {}
        for cat, provs in sidecar.items():
            for pid, st in provs.items():
                providers.append({
                    "category": cat, "id": pid,
                    "status": st.get("status", "unknown"),
                    "latencyMs": st.get("latencyMs"),
                    "lastTestAt": st.get("lastTestAt")})
    except Exception:
        pass

    # LTM memory stats per conf_uid (real SQLite counts)
    memories = []
    if _LTM_DIR.exists():
        for db in sorted(_LTM_DIR.glob("*.db")):
            entry = {"conf_uid": db.stem, "memories": None,
                     "keywords": None, "size_kb": round(
                         db.stat().st_size / 1024, 1)}
            try:
                import sqlite3
                conn = sqlite3.connect(str(db))
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM memories "
                            "WHERE status='active'")
                entry["memories"] = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM keywords")
                entry["keywords"] = cur.fetchone()[0]
                conn.close()
            except Exception:
                pass
            memories.append(entry)

    # services status
    services = [
        {"name": "后端 (12395)",
         "ok": _http_ok("http://127.0.0.1:12395/")[0],
         "url": "/"},
        {"name": "Provider 中心", "ok": _http_ok(
            "http://127.0.0.1:12395/providers/")[0], "url": "/providers/"},
        {"name": "屿禾工坊", "ok": _http_ok(
            "http://127.0.0.1:12395/workshop/")[0], "url": "/workshop/"},
        {"name": "记忆面板", "ok": _http_ok(
            "http://127.0.0.1:12395/memory/")[0], "url": "/memory/"},
        {"name": "配置面板", "ok": _http_ok(
            "http://127.0.0.1:12395/config/")[0], "url": "/config/"},
        {"name": "Web 工具", "ok": _http_ok(
            "http://127.0.0.1:12395/web-tool/index.html")[0],
         "url": "/web-tool/index.html"},
        {"name": "TLS 公网入口 (12393)", "ok": _http_ok(
            "https://127.0.0.1:12393/")[0], "url": "https://…:12393/"},
    ]

    git = _git_state()
    return {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "uptimeMinutes": int((time.time() - START) / 60),
        "devices": devices,
        "providerHealth": providers,
        "memories": memories,
        "services": services,
        "storage": {
            "ltm": _dir_stats(_LTM_DIR),
            "cache": _dir_stats(_CACHE_DIR),
            "logs": _dir_stats(_LOGS_DIR),
        },
        "git": git,
    }


# ---------------------------------------------------------------------------
# Logs: tail the backend log (read-only, sanitized)
# ---------------------------------------------------------------------------
_LOG_FILE = Path("/tmp/ollvm.log")


@router.get("/logs")
def get_logs(lines: int = 200, tag: str = "backend"):
    """Tail a known log. tags: backend | restart | tls"""
    files = {
        "backend": _LOG_FILE,
        "restart": Path("/tmp/ollvm-restart.log"),
        "tls": Path("/tmp/tls_proxy.log"),
    }
    path = files.get(tag)
    if path is None or not path.exists():
        return {"tag": tag, "lines": [], "note": "日志不存在"}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"tag": tag, "lines": [f"读取失败: {e}"], "note": "error"}
    # keep the LAST n lines; strip any key-like secrets defensively
    import re as _re
    tail = text.splitlines()[-max(1, min(lines, 1000)):]
    safe = []
    for line in tail:
        line = _re.sub(r"(sk-[A-Za-z0-9_\-]{8,})", "sk-***", line)
        line = _re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]{8,}",
                       r"\1***", line)
        safe.append(line)
    return {"tag": tag, "lines": safe, "count": len(safe)}


# ---------------------------------------------------------------------------
# System: host vitals + TLS control + backups
# ---------------------------------------------------------------------------
@router.get("/system")
def system_info():
    info = {"git": _git_state()}
    try:
        out = subprocess.run(
            ["free", "-m"], capture_output=True, text=True,
            timeout=5).stdout.splitlines()
        parts = out[1].split()
        info["memory"] = {
            "total_mb": int(parts[1]), "used_mb": int(parts[2]),
            "available_mb": int(parts[6])}
    except Exception:
        info["memory"] = None
    try:
        st = os.statvfs("/")
        total = st.f_frsize * st.f_blocks
        free = st.f_frsize * st.f_bavail
        used = total - free
        info["disk"] = {
            "total_gb": round(total / 1073741824, 1),
            "used_gb": round(used / 1073741824, 1),
            "avail_gb": round(free / 1073741824, 1),
            "use_pct": str(round(used * 100 / total)) + "%"}
    except Exception:
        info["disk"] = None
    try:
        out = subprocess.run(
            ["uptime"], capture_output=True, text=True,
            timeout=5).stdout.strip()
        info["uptime"] = out
    except Exception:
        info["uptime"] = None
    # TLS proxy state (self-signed: verify=False, request-level only)
    try:
        import ssl
        import urllib.request
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen("https://127.0.0.1:12393/",
                                    timeout=3, context=ctx) as r:
            info["tls"] = r.status == 200
    except Exception:
        info["tls"] = False
    # conf backups (newest 10)
    backups = []
    bdir = _CONF_DIR / "backups"
    if bdir.exists():
        for f in sorted(bdir.glob("conf-*.yaml"),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            backups.append({"name": f.name,
                            "mtime": datetime.fromtimestamp(
                                f.stat().st_mtime).isoformat(
                                timespec="minutes"),
                            "size_kb": round(f.stat().st_size / 1024, 1)})
            if len(backups) >= 10:
                break
    info["backups"] = backups
    return info


@router.post("/tls")
def tls_control(action: str = "status"):
    if action not in ("start", "stop", "restart", "status"):
        raise HTTPException(400, "action must be start|stop|restart|status")
    try:
        out = subprocess.run(
            ["bash", str(_CONF_DIR / "tlsctl.sh"), action],
            capture_output=True, text=True, timeout=30)
        return {"ok": out.returncode == 0,
                "output": (out.stdout + out.stderr).strip()[:500]}
    except Exception as e:
        return {"ok": False, "output": str(e)[:200]}


# ---------------------------------------------------------------------------
# Tools: TTS preview with parameters + apply voice to system config
# ---------------------------------------------------------------------------
import asyncio
import uuid
from pydantic import BaseModel


class TTSPreviewReq(BaseModel):
    text: str
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: str = "100"
    volume: str = "100"


_VOICE_WHITELIST = {
    "zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural", "zh-CN-YunxiNeural",
    "zh-CN-YunyangNeural", "zh-CN-YunjianNeural",
    "en-US-AvaMultilingualNeural", "ja-JP-NanamiNeural",
}


@router.post("/tts-preview")
def tts_preview(req: TTSPreviewReq):
    """Synthesize a preview with the given parameters (edge_tts).

    edge_tts itself supports voice + rate (pitch is fixed); volume
    is applied by ffmpeg post-processing so the preview matches what
    the user hears.
    """
    text = (req.text or "").strip()
    if not text or len(text) > 500:
        raise HTTPException(400, "text required (<=500 chars)")
    voice = req.voice if req.voice in _VOICE_WHITELIST \
        else "zh-CN-XiaoxiaoNeural"
    try:
        rate_val = int(float(req.rate))
        rate_val = max(-50, min(100, rate_val))   # edge rate: -50..100
    except (ValueError, TypeError):
        rate_val = 0
    try:
        vol = max(0.0, min(2.0, float(req.volume) / 100.0))
    except (ValueError, TypeError):
        vol = 1.0

    t0 = time.time()
    try:
        import sys
        src_dir = str(PROJECT_ROOT / "src")
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        from open_llm_vtuber.tts.edge_tts import TTSEngine
        engine = TTSEngine(voice=voice)
        name = "cc_tts_" + uuid.uuid4().hex[:8]
        raw_path = asyncio.run(
            engine.async_generate_audio(text, name))
        if not raw_path or not os.path.isfile(raw_path):
            return {"error": "synthesis produced no file",
                    "audio_url": None, "latency_ms": None}
        # apply rate/volume via ffmpeg into the cache dir
        out_name = "cc_tts_fx_" + uuid.uuid4().hex[:8] + ".mp3"
        out_path = _CACHE_DIR / out_name
        import subprocess as _sp
        cmd = ["ffmpeg", "-y", "-i", raw_path,
               "-filter:a",
               f"atempo={max(0.5, min(2.0, 1.0 + rate_val / 100.0))}"
               f",volume={vol:.2f}",
               str(out_path)]
        r = _sp.run(cmd, capture_output=True, timeout=60)
        if r.returncode != 0 or not out_path.exists():
            return {"error": "audio post-processing failed",
                    "audio_url": None, "latency_ms": None}
        return {"audio_url": "/cache/" + out_name,
                "latency_ms": int((time.time() - t0) * 1000),
                "error": None}
    except Exception as e:
        return {"error": str(e)[:200], "audio_url": None,
                "latency_ms": int((time.time() - t0) * 1000)}


class TTSApplyReq(BaseModel):
    voice: str


@router.post("/tts-apply")
def tts_apply(req: TTSApplyReq):
    """Persist the chosen voice into conf.yaml (tts_config.<engine>
    .voice) — ruamel comment-preserving, backed up, then restart."""
    voice = req.voice if req.voice in _VOICE_WHITELIST \
        else "zh-CN-XiaoxiaoNeural"
    if not CONF_PATH.exists():
        raise HTTPException(500, "conf.yaml not found")
    try:
        yaml_handler.preserve_quotes = True
        yaml_handler.width = 4096
        data = yaml_handler.load(StringIO(
            CONF_PATH.read_text(encoding="utf-8")))
    except Exception as e:
        raise HTTPException(500, f"conf parse error: {e}")
    tts = data.get("character_config", {}).get("tts_config")
    if not isinstance(tts, dict):
        raise HTTPException(500, "tts_config section missing")
    engine = tts.get("tts_model") or "edge_tts"
    engine_cfg = tts.get(engine)
    if isinstance(engine_cfg, dict):
        engine_cfg["voice"] = voice
    else:
        tts[engine] = {"voice": voice}
    buf = StringIO()
    yaml_handler.dump(data, buf)
    tmp = CONF_PATH.with_suffix(".yaml.tts-tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8")
    os.replace(tmp, CONF_PATH)
    # backup + restart (owner-safe)
    from datetime import datetime as _dt
    bdir = _CONF_DIR / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(CONF_PATH, bdir / ("conf-%s-tts-voice.yaml" %
                 _dt.now().strftime("%Y%m%d-%H%M%S")))
    script = _CONF_DIR / "restart_server.sh"
    subprocess.Popen(["bash", str(script)],
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return {"ok": True, "voice": voice,
            "message": "voice persisted; restarting (~40s)"}


# ---------------------------------------------------------------------------
# System: restart the backend (owner-safe)
# ---------------------------------------------------------------------------
@router.post("/restart")
def restart():
    script = _CONF_DIR / "restart_server.sh"
    if not script.exists():
        raise HTTPException(500, "restart script not found")
    subprocess.Popen(["bash", str(script)],
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return JSONResponse({"ok": True,
                         "message": "重启中，约 40 秒后刷新"})
