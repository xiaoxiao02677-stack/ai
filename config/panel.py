"""
Config Panel Router (config/panel.py)
=======================================

Web configuration panel for conf.yaml, served under /config on the public
HTTPS port 12393 (TLS proxy -> internal 127.0.0.1:12395). Mounted from
src/open_llm_vtuber/server.py before the frontend catch-all.

API routes (prefix /config/api):
    GET  /state       -> config tree (with YAML comments as descriptions) + backups
    GET  /raw         -> raw YAML text
    GET  /backups     -> list backup files
    POST /save        -> backup + validate + atomic write
    POST /restore     -> restore from backup
    POST /preview     -> validate YAML without saving
    POST /restart     -> trigger server restart (setsid, detached)
    GET  /status      -> process status + log tail

Frontend static files live in config/web/ (index.html, style.css, app.js),
served at /config/ via StaticFiles(html=True).
"""

import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from io import StringIO
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from ruamel.yaml import YAML
from ruamel.yaml.scanner import ScannerError
from ruamel.yaml.parser import ParserError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONF_PATH = PROJECT_ROOT / "conf.yaml"
BACKUP_DIR = Path(__file__).resolve().parent / "backups"
LOG_PATH = Path("/tmp/ollvm.log")
RESTART_SCRIPT = Path(__file__).resolve().parent / "restart_server.sh"

yaml_handler = YAML()
yaml_handler.preserve_quotes = True
yaml_handler.width = 4096

router = APIRouter(prefix="/config/api")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _atomic_write(text: str) -> None:
    tmp = CONF_PATH.with_suffix(".yaml.panel-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, CONF_PATH)


def _parse_yaml(text: str):
    return yaml_handler.load(StringIO(text))


def _dump_yaml(data) -> str:
    buf = StringIO()
    yaml_handler.dump(data, buf)
    return buf.getvalue()


def _make_backup(tag: str = "") -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"conf-{stamp}{('-' + tag) if tag else ''}.yaml"
    target = BACKUP_DIR / name
    shutil.copy2(CONF_PATH, target)
    backups = sorted(BACKUP_DIR.glob("conf-*.yaml"))
    for old in backups[:-30]:
        old.unlink()
    return name


def _extract_comment_map(raw_text: str) -> dict:
    """Scan raw YAML lines for '# key: comment' style descriptions.

    Returns {dotted.path: comment} where comment is the trailing #-comment
    on the line where that key is defined. Handles simple (non flow-style)
    lines only; keys inside lists are matched by the nearest parent key.
    """
    comments = {}
    stack = []  # (indent, key)
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") or stripped == "-":
            continue
        m = re.match(r"^([\w\.\- ]+?)\s*:", line)
        if not m:
            continue
        key = m.group(1).strip().strip("'\"")
        # only treat as a mapping key if the value part is empty/inline (not flow style)
        rest = line[m.end():]
        if rest.strip().startswith("{") or rest.strip().startswith("["):
            continue
        indent = len(line) - len(line.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([k for _, k in stack] + [key])
        cm = re.search(r"#\s*(.+?)\s*$", stripped)
        if cm:
            comments[path] = cm.group(1)
        stack.append((indent, key))
    return comments


def _tree(node):
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    if isinstance(node, CommentedMap):
        out = {}
        for key, value in node.items():
            out[str(key)] = _tree(value)
        return out
    if isinstance(node, CommentedSeq):
        return [_tree(item) for item in node]
    return node


def _validate_config_dict(data: dict) -> None:
    """Light structural validation matching the project's expectations."""
    if not isinstance(data, dict):
        raise ValueError("Config root must be a mapping")
    for section in ("system_config", "character_config"):
        if section not in data:
            raise ValueError(f"Missing required section: {section}")
    sys_conf = data.get("system_config", {})
    if not isinstance(sys_conf, dict):
        raise ValueError("system_config must be a mapping")
    port = sys_conf.get("port")
    if port is not None:
        try:
            p = int(port)
            if not (1 <= p <= 65535):
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("system_config.port must be an integer between 1-65535")
    char_conf = data.get("character_config")
    if char_conf is not None and not isinstance(char_conf, dict):
        raise ValueError("character_config must be a mapping")


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------

class SaveRequest(BaseModel):
    yaml_text: str


class RestoreRequest(BaseModel):
    backup: str


class PreviewRequest(BaseModel):
    yaml_text: str


class ApplyRequest(BaseModel):
    changes: dict  # {dotted.path: value} applied in-place, comments preserved


def _cast_like(current, incoming):
    """Cast incoming value to the type of the current YAML value."""
    if isinstance(current, bool):
        if isinstance(incoming, str):
            return incoming.strip().lower() in ("true", "yes", "1", "on")
        return bool(incoming)
    if isinstance(current, int) and not isinstance(current, bool):
        try:
            return int(incoming)
        except (TypeError, ValueError):
            return incoming
    if isinstance(current, float):
        try:
            return float(incoming)
        except (TypeError, ValueError):
            return incoming
    if isinstance(current, list):
        if isinstance(incoming, list):
            return incoming
        if isinstance(incoming, str):
            return [part.strip() for part in incoming.split(",") if part.strip()]
        return incoming
    if current is None and incoming == "":
        return None
    return incoming


def _apply_path(data, segments: list, value):
    node = data
    for seg in segments[:-1]:
        if not isinstance(node, dict) or seg not in node:
            raise ValueError(f"Path not found: {'.'.join(segments)}")
        node = node[seg]
    leaf = segments[-1]
    if not isinstance(node, dict) or leaf not in node:
        raise ValueError(f"Path not found: {'.'.join(segments)}")
    node[leaf] = _cast_like(node[leaf], value)


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

@router.get("/state")
async def get_state():
    if not CONF_PATH.exists():
        raise HTTPException(404, "conf.yaml not found")
    raw = CONF_PATH.read_text(encoding="utf-8")
    data = _parse_yaml(raw)
    if data is None:
        raise HTTPException(500, "conf.yaml parsed as empty")
    backups = (
        sorted(p.name for p in BACKUP_DIR.glob("conf-*.yaml")) if BACKUP_DIR.exists() else []
    )
    return {
        "tree": _tree(data),
        "raw": raw,
        "comments": _extract_comment_map(raw),
        "backups": backups,
        "mtime": datetime.fromtimestamp(CONF_PATH.stat().st_mtime).isoformat(timespec="seconds"),
        "size": CONF_PATH.stat().st_size,
    }


@router.get("/raw")
async def get_raw():
    if not CONF_PATH.exists():
        raise HTTPException(404, "conf.yaml not found")
    return JSONResponse({"raw": CONF_PATH.read_text(encoding="utf-8")})


@router.get("/backups")
async def get_backups():
    if not BACKUP_DIR.exists():
        return {"backups": []}
    items = []
    for p in sorted(BACKUP_DIR.glob("conf-*.yaml"), reverse=True):
        items.append(
            {
                "name": p.name,
                "size": p.stat().st_size,
                "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
    return {"backups": items}


@router.post("/apply")
async def apply_changes(req: ApplyRequest):
    """Form mode: apply {path: value} changes with comments preserved."""
    raw = CONF_PATH.read_text(encoding="utf-8")
    data = _parse_yaml(raw)
    if data is None:
        raise HTTPException(500, "conf.yaml parsed as empty")
    applied = 0
    errors = []
    for path, value in req.changes.items():
        segments = [s for s in path.split(".") if s]
        try:
            _apply_path(data, segments, value)
            applied += 1
        except ValueError as e:
            errors.append(str(e))
    if errors:
        return JSONResponse({"ok": False, "errors": errors, "applied": applied}, status_code=400)
    _validate_config_dict(data)
    _make_backup("apply")
    _atomic_write(_dump_yaml(data))
    return {"ok": True, "applied": applied}


@router.post("/save")
async def save_config(req: SaveRequest):
    text = req.yaml_text
    if not text or not text.strip():
        raise HTTPException(400, "Empty YAML")
    try:
        data = _parse_yaml(text)
    except (ScannerError, ParserError) as e:
        raise HTTPException(400, f"YAML syntax error: {e}")
    if data is None:
        raise HTTPException(400, "YAML parsed as empty document")
    try:
        _validate_config_dict(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    backup = _make_backup("manual")
    _atomic_write(text)
    return {"ok": True, "backup": backup}


@router.post("/restore")
async def restore_config(req: RestoreRequest):
    name = req.backup
    if "/" in name or ".." in name or not name.startswith("conf-"):
        raise HTTPException(400, "Invalid backup name")
    src = BACKUP_DIR / name
    if not src.exists():
        raise HTTPException(404, f"Backup {name} not found")
    _make_backup("pre-restore")
    shutil.copy2(src, CONF_PATH)
    return {"ok": True, "restored": name}


@router.post("/preview")
async def preview_config(req: PreviewRequest):
    try:
        data = _parse_yaml(req.yaml_text)
        if data is None:
            return {"ok": False, "error": "YAML parsed as empty document"}
        try:
            _validate_config_dict(data)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "roundtrip": _dump_yaml(data)}
    except (ScannerError, ParserError) as e:
        return {"ok": False, "error": f"YAML syntax error: {e}"}


@router.post("/restart")
async def restart_server():
    if not RESTART_SCRIPT.exists():
        raise HTTPException(500, f"restart script missing: {RESTART_SCRIPT}")
    _make_backup("pre-restart")

    def _run():
        time.sleep(0.8)
        subprocess.run(
            ["bash", str(RESTART_SCRIPT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "message": "Restart initiated, page will auto-reconnect"}


@router.get("/status")
async def get_status():
    proc = subprocess.run(
        ["pgrep", "-af", "run_server.py"], capture_output=True, text=True
    )
    procs = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    log_tail = ""
    if LOG_PATH.exists():
        try:
            log_tail = "\n".join(
                LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
            )
        except Exception:
            log_tail = "(failed to read log)"
    return {
        "running": bool(procs),
        "processes": procs,
        "port": 12395,  # internal backend port; public HTTPS entry is 12393 via tls_proxy
        "log_tail": log_tail,
    }
