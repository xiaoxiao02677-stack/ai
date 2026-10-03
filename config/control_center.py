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
