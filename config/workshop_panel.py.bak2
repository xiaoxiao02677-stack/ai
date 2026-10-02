"""YH Workshop (屿禾工坊) — ESP32 device management console backend.

The device control center of the AI-companion system: device registry,
health, capability intersection, command history, live events, LED
control through the FULL P3-P19 chain, and the P19-R acceptance flow.

Architecture (workshop spec):
  - SINGLE protocol implementation: all wire/protocol logic comes from
    open_llm_vtuber.device_protocol (P15-P18) and p19r_service — this
    panel adds NO second DeviceCommand/DeviceAck/codec.
  - LED control goes through the FULL chain: a workshop ActionIntent is
    created, persisted through the P9 decision/P8 evaluation/P7
    strategy chain entries (workshop-originated provenance), and
    executed by ExecutionGateway.execute() — Policy decides, the
    Gateway routes, the ESP32Adapter talks TCP. There is NO direct
    socket path from any web API.
  - In-memory device runtime: the panel keeps a process-level
    DeviceRegistry + DeviceObserver (the same P17/P18 objects). Real
    devices connect (server OUTBOUND -> device:3333 listener firmware)
    — but the workshop also supports "console-coupled" sessions: each
    P19-R validation run registers the device live into the runtime.
  - No persistence added: DeviceState/Health/Session/Lifecycle stay
    in-memory derived (P17/P18 semantics preserved).

API (prefix /workshop/api):
  GET  /overview                     -> totals + recent commands
  GET  /devices                      -> device cards
  GET  /devices/{id}                 -> detail incl. health/capabilities
  GET  /devices/{id}/health          -> P18 DeviceHealth (computed)
  GET  /devices/{id}/capabilities    -> server contract vs advertisement
  GET  /devices/{id}/commands        -> P18 command history (no params)
  GET  /devices/{id}/events          -> recent events (poll-based)
  GET  /devices/{id}/protocol        -> last protocol frames (masked)
  GET  /devices/{id}/security        -> policy/gateway/gate status
  GET  /devices/{id}/logs            -> level-filtered event log
  POST /devices/{id}/led             -> full-chain SET_LED {on: bool}
  POST /devices/{id}/diagnostics     -> read-only diagnostics
  POST /devices/{id}/p19r            -> full acceptance run (service)
  POST /connect                      -> attach a device by host[:port]

Kill switch is NEVER mutable from here (display only).
"""

import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from src.open_llm_vtuber.device_protocol import (
        DeviceRegistry, DeviceObserver, DeviceHello, CapabilityAdvertisement,
        DeviceCommandError, GATE_OK)
    from src.open_llm_vtuber.device_protocol.session import Heartbeat
    from src.open_llm_vtuber.device_protocol.p19r_service import (
        XiaozhiCodec, DeviceConnection, validate_device)
except ImportError:  # in-package fallback
    from open_llm_vtuber.device_protocol import (  # type: ignore
        DeviceRegistry, DeviceObserver, DeviceHello, CapabilityAdvertisement,
        DeviceCommandError, GATE_OK)
    from open_llm_vtuber.device_protocol.session import Heartbeat  # type: ignore
    from open_llm_vtuber.device_protocol.p19r_service import (  # type: ignore
        XiaozhiCodec, DeviceConnection, validate_device)

try:
    from config.device_gateway import DeviceGateway
except ImportError:
    try:
        from device_gateway import DeviceGateway  # type: ignore
    except ImportError:
        DeviceGateway = None  # type: ignore

router = APIRouter(prefix="/workshop/api")

# ---------------------------------------------------------------------------
# process-level device runtime (the ONLY instance; in-memory per P17/P18)
# ---------------------------------------------------------------------------

registry = DeviceRegistry()
observer = DeviceObserver(registry)

# inbound device gateway (TCP :3333) — client-mode firmware connects TO
# the server. Started once at import; devices attach via _attach_device.
gateway = None
if DeviceGateway is not None:
    gateway = DeviceGateway(
        host="0.0.0.0", port=3333,
        on_device_attach=lambda hello, adv, ip, port, wire: _attach_device(
            hello, adv, ip, port, wire))
    gateway.start()


def refresh_device_activity(device_id: str) -> None:
    """A heartbeat/any frame from the gateway refreshes the session."""
    try:
        registry.heartbeat(
            Heartbeat(device_id=device_id), now=time.time())
    except Exception:
        pass

# server-side capability contracts (static, from the P12 registry)
try:
    from src.open_llm_vtuber.capability import DEFAULT_REGISTRY as _CAPS
except ImportError:
    from open_llm_vtuber.capability import DEFAULT_REGISTRY as _CAPS  # type: ignore


class _DeviceMeta:
    """Non-protocol metadata per device (network origin, log, frames)."""

    def __init__(self):
        self.source_ip: Optional[str] = None
        self.source_port: Optional[int] = None
        self.connected_at: Optional[float] = None
        self.wire_session_id: Optional[str] = None
        self.events: List[Dict[str, Any]] = []   # bounded event log

    def log(self, level: str, event: str, device_id: str = "") -> None:
        self.events.append({
            "ts": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "device": device_id,
            "event": event,
        })
        if len(self.events) > 200:
            del self.events[:100]


_meta: Dict[str, _DeviceMeta] = {}
_lock = threading.Lock()


def _meta_for(device_id: str) -> _DeviceMeta:
    with _lock:
        if device_id not in _meta:
            _meta[device_id] = _DeviceMeta()
        return _meta[device_id]


def _attach_device(hello: DeviceHello, adv: Optional[CapabilityAdvertisement],
                   ip: Optional[str], port: Optional[int],
                   wire_session_id: Optional[str] = None) -> None:
    """Register/couple a live device into the workshop runtime."""
    registry.register_hello(hello, now=time.time())
    if adv is not None:
        registry.advertise(adv, now=time.time())
    m = _meta_for(hello.device_id)
    if m.connected_at is None:
        m.connected_at = time.time()
    if ip:
        m.source_ip = ip
    if port:
        m.source_port = port
    if wire_session_id:
        m.wire_session_id = wire_session_id
    m.log("INFO", "device online (HELLO received)", hello.device_id)
    if adv is not None:
        m.log("INFO",
              f"capability advertised: {adv.operations}",
              hello.device_id)


# ---------------------------------------------------------------------------
# full-chain LED execution (workshop-originated intent through the gateway)
# ---------------------------------------------------------------------------
# full-chain LED execution (workshop-originated intent through the gateway)
# ---------------------------------------------------------------------------

def _run_led_full_chain(device_id: str, on: bool) -> Dict[str, Any]:
    """SET_LED via the P3-P19 chain. NEVER a direct socket send.

    Provenance: the workshop fabricates the full stored chain
    (strategy <- evaluation <- decision <- action intent), exactly as
    the P10-P13 test suites do for manual intents — then hands
    execution to ExecutionGateway.execute(), which re-verifies
    provenance, resolves the capability, validates input, applies the
    policy (kill switch!) and runs the adapter.
    """
    try:
        from src.open_llm_vtuber.long_term_memory.store import MemoryStore
        from src.open_llm_vtuber.strategy.repository import \
            StrategyRepository
        from src.open_llm_vtuber.strategy.schemas import StrategyRecord
        from src.open_llm_vtuber.evaluation.repository import \
            EvaluationRepository
        from src.open_llm_vtuber.evaluation.schemas import EvaluationRecord
        from src.open_llm_vtuber.decision.repository import \
            DecisionRepository
        from src.open_llm_vtuber.decision.schemas import DecisionRecord, \
            STATUS_SELECTED as DEC_SELECTED
        from src.open_llm_vtuber.action.repository import ActionRepository
        from src.open_llm_vtuber.action.schemas import ActionIntentRecord, \
            STATUS_PLANNED
        from src.open_llm_vtuber.execution.gateway import ExecutionGateway
        from src.open_llm_vtuber.execution.repository import \
            ExecutionRepository
    except ImportError:
        from open_llm_vtuber.long_term_memory.store import MemoryStore  # type: ignore
        from open_llm_vtuber.strategy.repository import \
            StrategyRepository  # type: ignore
        from open_llm_vtuber.strategy.schemas import StrategyRecord  # type: ignore
        from open_llm_vtuber.evaluation.repository import \
            EvaluationRepository  # type: ignore
        from open_llm_vtuber.evaluation.schemas import EvaluationRecord  # type: ignore
        from open_llm_vtuber.decision.repository import \
            DecisionRepository  # type: ignore
        from open_llm_vtuber.decision.schemas import DecisionRecord, \
            STATUS_SELECTED as DEC_SELECTED  # type: ignore
        from open_llm_vtuber.action.repository import \
            ActionRepository  # type: ignore
        from open_llm_vtuber.action.schemas import ActionIntentRecord, \
            STATUS_PLANNED  # type: ignore
        from open_llm_vtuber.execution.gateway import ExecutionGateway  # type: ignore
        from open_llm_vtuber.execution.repository import \
            ExecutionRepository  # type: ignore

    conf_uid = "workshop"
    store = MemoryStore(conf_uid)
    strategy_repo = StrategyRepository(store.provider)
    evaluation_repo = EvaluationRepository(store.provider)
    decision_repo = DecisionRepository(store.provider)
    action_repo = ActionRepository(store.provider)
    execution_repo = ExecutionRepository(store.provider)

    # --- build the stored provenance chain (workshop-originated) ---
    strategy = StrategyRecord.new(conf_uid, ["workshop-manual"])
    strategy.condition = "屿禾工坊手动设备控制"
    strategy.recommendation = "点亮设备 LED" if on else "熄灭设备 LED"
    strategy.evidence = ["workshop-manual"]
    strategy.confidence = 0.9
    strategy_repo.save(strategy)

    evaluation = EvaluationRecord.new(conf_uid, strategy.strategy_id)
    evaluation.applicable = True
    evaluation.relevance = 0.9
    evaluation.confidence = 0.85
    evaluation.condition_match = 0.7
    evaluation.reason = "工坊手动控制"
    evaluation_repo.save(evaluation)

    decision = DecisionRecord.new(conf_uid, DEC_SELECTED)
    decision.selected_evaluation_id = evaluation.evaluation_id
    decision.selected_strategy_id = strategy.strategy_id
    decision.confidence = 0.8
    decision.reason = "工坊选择执行"
    decision_repo.save(decision)

    intent = ActionIntentRecord.new(conf_uid, decision.decision_id,
                                    "SET_LED")
    intent.evaluation_id = evaluation.evaluation_id
    intent.strategy_id = strategy.strategy_id
    intent.parameters = {"on": on}
    intent.reason = "屿禾工坊 LED 控制"
    intent.status = STATUS_PLANNED
    action_repo.save(intent)

    # --- the ONE execution boundary ---
    gateway = ExecutionGateway(execution_repo, action_repo, decision_repo,
                               evaluation_repo)
    result = gateway.execute(intent.action_id, conf_uid)
    if result is None:
        raise HTTPException(500, "gateway returned no result")

    m = _meta_for(device_id)
    m.log("INFO",
          f"command SET_LED on={int(on)} -> {result.status}",
          device_id)
    return {
        "status": result.status,
        "reason": result.reason,
        "action_id": intent.action_id,
        "capability": "capability.led",
        "operation": "SET_LED",
        "policy_metadata": result.metadata or {},
    }


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------

class LedRequest(BaseModel):
    on: bool


class ConnectRequest(BaseModel):
    host: str
    port: int = 3333
    timeout: float = 6.0


class P19RRequest(BaseModel):
    timeout: float = 6.0
    expect_device_id: Optional[str] = None
    # physical confirmations supplied by the web UI (two phases)
    confirm_led_on: bool = False
    confirm_led_off: bool = False


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _require_device(device_id: str):
    session = registry.get(device_id)
    if session is None:
        raise HTTPException(404, f"device '{device_id}' not found "
                                 f"(no session — connect the device or "
                                 f"run a P19-R validation first)")
    return session


def _state_dict(device_id: str) -> Dict[str, Any]:
    st = observer.get_device_state(device_id)
    if st is None:
        return {}
    return {
        "device_id": st.device_id,
        "session_id": st.session_id,
        "device_type": st.device_type,
        "firmware_version": st.firmware_version,
        "protocol_version": st.protocol_version,
        "connection_state": st.connection_state,
        "last_seen": st.last_seen,
        "capabilities": st.capabilities,
        "last_command_status": st.last_command_status,
        "last_ack_status": st.last_ack_status,
    }


def _health_dict(device_id: str) -> Dict[str, Any]:
    hp, code = observer.get_device_health(device_id)
    if hp is None:
        return {"code": code}
    ready, gate_code = observer.device_ready(device_id, "SET_LED",
                                             now=time.time())
    return {
        "code": code,
        "online": hp.online,
        "connection_state": hp.health_state,
        "heartbeat_age": round(hp.heartbeat_age, 1),
        "protocol_compatible": hp.protocol_compatible,
        "capability_valid": hp.capability_valid,
        "readiness": {"ready": ready, "gate_code": gate_code},
    }


def _device_summary(device_id: str) -> Dict[str, Any]:
    m = _meta_for(device_id)
    d = _state_dict(device_id)
    hp = observer.get_device_health(device_id)[0]
    return {
        **d,
        "ip": m.source_ip or "未连接",
        "port": m.source_port,
        "online": bool(hp.online) if hp else False,
        "display_name": f"小智 {d.get('device_type', '')}".strip(),
    }


def _recent_commands(device_id: str) -> List[Dict[str, Any]]:
    history = observer.history    # same-process observer (bounded FIFO 64)
    out = []
    for rec in list(history._records.values()):
        if rec.device_id != device_id:
            continue
        out.append({
            "command_id": rec.command_id,
            "device_id": rec.device_id,
            "operation": rec.operation,
            "status": rec.status,
            "created_at": rec.created_at,
            "sent_at": rec.sent_at,
            "completed_at": rec.completed_at,
            "error_code": rec.error_code,
            "late_ack": rec.late_ack,
            "duration_ms": (int((rec.completed_at - rec.created_at)
                                * 1000)
                            if rec.completed_at and rec.created_at
                            else None),
        })
    return out[-50:]


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

@router.get("/overview")
async def overview():
    registry.check_stale(now=time.time())
    device_ids = list(registry._sessions.keys())
    states = []
    for did in device_ids:
        hp = observer.get_device_health(did)[0]
        if hp is not None:
            states.append(hp.health_state)
    recent = []
    for did in device_ids:
        recent.extend(_recent_commands(did))
    recent.sort(key=lambda c: c.get("created_at") or 0, reverse=True)
    return {
        "totals": {
            "devices": len(device_ids),
            "online": states.count("ONLINE"),
            "stale": states.count("STALE"),
            "disconnected": states.count("DISCONNECTED"),
        },
        "devices": [_device_summary(d) for d in device_ids],
        "recent_commands": recent[:10],
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "device_gateway": {
            "listening": gateway is not None
            and gateway.started_at is not None,
            "host": "0.0.0.0", "port": 3333,
        } if gateway is not None else {"listening": False},
    }


@router.get("/devices")
async def list_devices():
    registry.check_stale(now=time.time())
    return {"devices": [_device_summary(d)
                        for d in registry._sessions.keys()]}


@router.get("/devices/{device_id}")
async def device_detail(device_id: str):
    session = _require_device(device_id)
    m = _meta_for(device_id)
    return {
        **_device_summary(device_id),
        "connected_at": m.connected_at,
        "wire_session_id": m.wire_session_id,
        "health": _health_dict(device_id),
        "transport": {"type": "TCP", "port": m.source_port or 3333},
    }


@router.get("/devices/{device_id}/health")
async def device_health(device_id: str):
    _require_device(device_id)
    return _health_dict(device_id)


@router.get("/devices/{device_id}/capabilities")
async def device_capabilities(device_id: str):
    _require_device(device_id)
    session = registry.get(device_id)
    server_caps = sorted(_CAPS.list_ids())
    advertised = session.capabilities
    device_ops = sorted(advertised)
    # the LED operation maps to capability.led on the server side
    server_ops = []
    for cap_id in server_caps:
        contract = _CAPS.get(cap_id)
        if contract is not None:
            server_ops.append(contract.capability_type)
    effective = sorted(set(server_ops) & set(device_ops))
    return {
        "server_capabilities": server_caps,
        "server_operations": sorted(set(server_ops)),
        "device_advertisement": device_ops,
        "effective_operations": effective,
        "note": "有效能力 = Server Capability ∩ Device Advertisement",
    }


@router.get("/devices/{device_id}/commands")
async def device_commands(device_id: str):
    _require_device(device_id)
    return {"commands": _recent_commands(device_id)}


@router.get("/devices/{device_id}/events")
async def device_events(device_id: str):
    _require_device(device_id)
    m = _meta_for(device_id)
    return {"events": m.events[-60:]}


@router.get("/devices/{device_id}/protocol")
async def device_protocol(device_id: str):
    _require_device(device_id)
    session = registry.get(device_id)
    m = _meta_for(device_id)
    return {
        "protocol_version": session.protocol_version,
        "connection_state": session.state,
        "last_seen": session.last_seen,
        "session_id": session.session_id,
        "last_hello": {"received": m.connected_at is not None,
                       "firmware": session.firmware_version},
        "last_advertisement": {"operations":
                               sorted(session.capabilities)},
        "transport": {"type": "TCP", "port": m.source_port or 3333},
    }


@router.get("/devices/{device_id}/security")
async def device_security(device_id: str):
    _require_device(device_id)
    try:
        from src.open_llm_vtuber.execution.policy import (
            GLOBAL_EXECUTION_ENABLED, ExecutionPolicy)
    except ImportError:
        from open_llm_vtuber.execution.policy import (  # type: ignore
            GLOBAL_EXECUTION_ENABLED, ExecutionPolicy)
    gate = registry.send_allowed(device_id, "SET_LED", now=time.time())
    return {
        "global_execution_enabled": GLOBAL_EXECUTION_ENABLED,
        "execution_policy": "ExecutionPolicy (default deny; kill switch "
                            "is a code-level constant)",
        "gateway": "ExecutionGateway (single boundary; workshop LED "
                   "commands route through it)",
        "device_gate": {"allowed": gate[0], "code": gate[1]},
        "note": "UI 只读：kill switch 不可从 Web 修改",
    }


@router.get("/devices/{device_id}/logs")
async def device_logs(device_id: str, level: str = "all"):
    _require_device(device_id)
    m = _meta_for(device_id)
    events = m.events
    if level in ("INFO", "WARN", "ERROR"):
        events = [e for e in events if e["level"] == level]
    return {"logs": events[-100:]}


@router.post("/devices/{device_id}/led")
async def device_led(device_id: str, req: LedRequest):
    """Full-chain SET_LED. Reads kill switch through the policy — the
    UI cannot bypass it; the honest result (REJECTED while the switch
    is OFF) is returned to the caller."""
    _require_device(device_id)
    return _run_led_full_chain(device_id, req.on)


@router.post("/devices/{device_id}/diagnostics")
async def device_diagnostics(device_id: str, req: Dict[str, Any] = None):
    """READ-ONLY diagnostics: connection / capability / heartbeat /
    protocol checks against the LIVE runtime state (no commands sent;
    never an arbitrary-command sender)."""
    _require_device(device_id)
    registry.check_stale(now=time.time())
    checks = []
    session = registry.get(device_id)
    checks.append({"name": "连接状态", "ok": session.state == "ONLINE",
                   "detail": session.state})
    checks.append({"name": "设备能力", "ok": "SET_LED" in
                   session.capabilities,
                   "detail": f"advertised: "
                             f"{sorted(session.capabilities)}"})
    hp, code = observer.get_device_health(device_id)
    checks.append({"name": "心跳检查", "ok": hp is not None and hp.online,
                   "detail": f"age={round(hp.heartbeat_age, 1)}s"
                             if hp else code})
    checks.append({"name": "协议版本", "ok": session.protocol_version == 1,
                   "detail": f"v{session.protocol_version}"})
    return {"checks": checks,
            "all_ok": all(c["ok"] for c in checks)}


@router.post("/connect")
async def connect_device(req: ConnectRequest):
    """Probe a device by address: connect, read HELLO + ADVERTISEMENT,
    and attach it to the workshop runtime. READ + session-register only
    (no commands)."""
    try:
        conn = DeviceConnection(req.host, req.port, req.timeout)
    except OSError as e:
        raise HTTPException(502, f"无法连接 {req.host}:{req.port}: {e}")
    try:
        frames = conn.read_frames(idle_wait=1.0, max_frames=3,
                                  hard_deadline=req.timeout)
        hello_doc = next((f for f in frames
                          if isinstance(f, dict)
                          and (f.get("type") == "HELLO"
                               or f.get("message_type")
                               == "device_hello")), None)
        if hello_doc is None:
            raise HTTPException(502, "设备未发送 HELLO")
        try:
            hello, wire_session = XiaozhiCodec.parse_hello(hello_doc)
        except Exception as e:
            raise HTTPException(502, f"HELLO 无效: {e}")
        adv = None
        adv_doc = next((f for f in frames
                        if isinstance(f, dict)
                        and (f.get("type") == "ADVERTISEMENT"
                             or f.get("message_type")
                             == "capability_advertisement")), None)
        if adv_doc:
            try:
                adv = XiaozhiCodec.parse_advertisement(
                    adv_doc, hello.device_id)
            except Exception:
                adv = None
        _attach_device(hello, adv, req.host, req.port, wire_session)
        return {"ok": True, "device_id": hello.device_id,
                "firmware": hello.firmware_version,
                "operations": adv.operations if adv else []}
    finally:
        conn.close()


@router.post("/devices/{device_id}/p19r")
async def device_p19r(device_id: str, req: P19RRequest):
    """Run the FULL P19-R acceptance sequence (single implementation in
    p19r_service). The two physical-LED confirmations arrive as flags
    from the web UI. The device is attached to the runtime as a side
    effect of the validation's session registration."""
    session = registry.get(device_id)
    if session is None:
        raise HTTPException(404, f"device '{device_id}' not found")
    ip = _meta_for(device_id).source_ip or "127.0.0.1"
    port = _meta_for(device_id).source_port or 3333
    # inbound link (client-mode firmware): validate over the gateway
    # channel instead of an outbound connection
    channel = gateway.get_channel(device_id) if gateway is not None else None

    steps: List[Dict[str, Any]] = []
    passed = failed = 0
    fatal = None
    known = None
    if channel is not None:
        sess = registry.get(device_id)
        if sess is not None:
            known = {
                "hello": DeviceHello(
                    device_id=device_id,
                    device_type=sess.device_type or "xiaozhi.esp32s3",
                    firmware_version=sess.firmware_version or "unknown"),
                "operations": list(sess.capabilities),
            }
    gen = validate_device(ip, port, timeout=req.timeout,
                          expected_device_id=device_id,
                          channel=channel, known_session=known)
    to_send = None
    try:
        while True:
            event = gen.send(to_send)
            to_send = None
            kind = event["kind"]
            if kind == "section":
                steps.append({"kind": "section",
                              "title": event["title"]})
            elif kind == "check":
                if event["ok"]:
                    passed += 1
                else:
                    failed += 1
                steps.append({"kind": "check", "name": event["name"],
                              "ok": event["ok"],
                              "detail": event.get("detail", "")})
            elif kind == "info":
                steps.append({"kind": "info", "text": event["text"]})
            elif kind == "device":
                steps.append({"kind": "device",
                              "state": event["state"]})
                # attach the live device into the workshop runtime
                sess = registry.get(event["device_id"])
                if sess is None:
                    adv = CapabilityAdvertisement(
                        device_id=event["device_id"],
                        operations=event["state"]["capabilities"])
                    _attach_device(
                        DeviceHello(
                            device_id=event["device_id"],
                            device_type=event["state"].get(
                                "device_type", "xiaozhi.esp32s3"),
                            firmware_version=event["state"].get(
                                "firmware_version", "unknown")),
                        adv, ip, port)
            elif kind == "led_confirm":
                to_send = (req.confirm_led_on
                           if event["phase"] == "on"
                           else req.confirm_led_off)
                steps.append({"kind": "led_confirm",
                              "phase": event["phase"],
                              "confirmed": to_send})
            elif kind == "fatal":
                fatal = event["reason"]
                steps.append({"kind": "fatal", "reason": fatal})
    except StopIteration:
        pass
    except OSError as e:
        steps.append({"kind": "fatal", "reason": f"connection error: {e}"})
        failed += 1

    m = _meta_for(device_id)
    m.log("INFO" if failed == 0 else "WARN",
          f"P19-R validation run: {passed} passed / {failed} failed",
          device_id)
    return {"steps": steps, "passed": passed, "failed": failed,
            "fatal": fatal,
            "physical_led": {
                "on_confirmed": req.confirm_led_on,
                "off_confirmed": req.confirm_led_off}}
