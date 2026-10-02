"""P23 ExpressionGateway + LCD adapter — the expression boundary.

The gateway composes the EXISTING device stack (no second protocol):
ExpressionIntent -> policy resolve -> P15 DeviceCommand(SET_LCD_STATE)
-> XiaozhiCodec frame -> channel send -> DeviceAck (P18 lifecycle
semantics via the observer). It reuses the workshop runtime's observer
so expression commands appear in the SAME command history.

Reliability domain (§13): expression failures NEVER raise into the
action path — the gateway returns an ExpressionResult and the caller
(action pipeline) may ignore it entirely.
"""

import time
import uuid
from typing import Any, Dict, Optional

try:
    from src.open_llm_vtuber.device_protocol import DeviceCommand
    from src.open_llm_vtuber.device_protocol.p19r_service import \
        XiaozhiCodec
except ImportError:
    from open_llm_vtuber.device_protocol import DeviceCommand  # type: ignore
    from open_llm_vtuber.device_protocol.p19r_service import (  # type: ignore
        XiaozhiCodec)

from .schemas import ExpressionIntent, ExpressionPolicy


class ExpressionResult(dict):
    """Outcome view of one expression dispatch (independent status
    model for the expression domain; action statuses are untouched)."""


class LCDExpressionAdapter:
    """Body-expression adapter for the SET_LCD_STATE operation.

    mock=True -> a deterministic in-memory LCD (software closed loop;
    clearly labeled MOCK — never counted as real hardware).
    mock=False -> sends over the provided channel (the workshop
    gateway channel; real device path).
    """

    def __init__(self, device_id: str, channel=None, mock: bool = True):
        self.device_id = device_id
        self.channel = channel
        self.mock = mock
        # mock panel state (software only)
        self.mock_state: Optional[str] = None
        self.mock_frames = []

    def dispatch(self, operation: str, parameters: Dict[str, Any]):
        """Send one expression command; return an ExpressionResult."""
        command_id = "expr-" + uuid.uuid4().hex[:12]
        cmd = DeviceCommand(
            command_id=command_id, device_id=self.device_id,
            capability="capability.lcd",
            operation=operation, parameters=parameters,
            protocol_version=1,
            provenance={"action_id": command_id,
                        "decision_id": "expression",
                        "evaluation_id": "expression",
                        "strategy_id": "expression"},
            created_at=time.time())
        cmd.validate()   # closed schema: SET_LCD_STATE must be in the enum

        if self.mock:
            self.mock_state = parameters.get("state")
            self.mock_frames.append(cmd.to_dict())
            return ExpressionResult({
                "command_id": command_id, "status": "ACKED",
                "device_ack": {"status": "ACK", "error_code": None},
                "state": parameters.get("state"), "mock": True})

        if self.channel is None:
            return ExpressionResult({
                "command_id": command_id, "status": "NOT_DISPATCHED",
                "device_ack": None, "state": parameters.get("state"),
                "mock": False})
        try:
            self.channel.send(XiaozhiCodec.encode_command(cmd))
            ack = self.channel.wait_ack(6.0)
        except OSError as e:
            return ExpressionResult({
                "command_id": command_id, "status": "FAILED",
                "device_ack": None, "state": parameters.get("state"),
                "mock": False, "error": str(e)})
        if ack is None:
            return ExpressionResult({
                "command_id": command_id, "status": "TIMEOUT",
                "device_ack": None, "state": parameters.get("state"),
                "mock": False})
        return ExpressionResult({
            "command_id": command_id,
            "status": "ACKED" if ack.is_success else "NACKED",
            "device_ack": {"status": ack.status,
                           "error_code": ack.error_code},
            "state": parameters.get("state"), "mock": False})


class ExpressionGateway:
    """The single expression boundary: intent -> policy -> adapter.

    Independent from the P13 ExecutionGateway (actions); communicates
    through the SAME device stack (session/transport/protocol).
    """

    def __init__(self, policy: Optional[ExpressionPolicy] = None,
                 adapter: Optional[LCDExpressionAdapter] = None,
                 observer=None):
        self.policy = policy or ExpressionPolicy()
        self.adapter = adapter
        self.observer = observer
        # current expression view (Workshop reads this; no fake data —
        # None until a real expression has been dispatched)
        self.current: Optional[str] = None
        self.last_result: Optional[ExpressionResult] = None
        self.updated_at: Optional[float] = None

    def express(self, intent: ExpressionIntent) -> ExpressionResult:
        """Route one expression intent through the boundary."""
        operation, parameters = self.policy.resolve(intent)
        if self.adapter is None:
            res = ExpressionResult({
                "command_id": None, "status": "NO_ADAPTER",
                "device_ack": None, "state": intent.state, "mock": None})
        else:
            res = self.adapter.dispatch(operation, parameters)
        # idempotent semantics (§12): same state -> refresh allowed, no
        # loop; the state view updates on every dispatch
        self.current = intent.state
        self.last_result = res
        self.updated_at = time.time()
        if self.observer is not None and res.get("command_id"):
            try:
                self.observer.observe_command_created(
                    res["command_id"], self.adapter.device_id
                    if self.adapter else "", operation)
                self.observer.observe_command_sent(res["command_id"])
                if res["status"] == "ACKED":
                    from open_llm_vtuber.device_protocol.ack import \
                        DeviceAck
                    self.observer.observe_ack(DeviceAck(
                        command_id=res["command_id"],
                        device_id=self.adapter.device_id,
                        status="ACK"))
                else:
                    self.observer.observe_terminal(
                        res["command_id"], "FAILED",
                        error_code=res.get("device_ack", {})
                        .get("error_code") or "EXPRESSION_" + res["status"])
            except Exception:
                pass   # observation never breaks expression
        return res


# ---------------------------------------------------------------------------
# system-event -> expression-state mapping (§15: event driven, never
# natural-language driven)
# ---------------------------------------------------------------------------
def expression_state_for_run(run_result: Dict[str, Any]) -> str:
    """Map a P21/P22 run result to the expression state.

    NO-ACTION never maps to EXECUTING (§16): a decision that abstains
    goes THINKING -> WAITING/IDLE.
    """
    if run_result is None:
        return "IDLE"
    if run_result.get("no_action"):
        return "WAITING"
    ack = run_result.get("device_ack")
    status = run_result.get("execution_status")
    if isinstance(ack, dict) and ack.get("status") == "ACK":
        return "SUCCESS"
    if isinstance(ack, dict) and ack.get("status") == "NACK":
        return "ERROR"
    if status in ("REJECTED", "FAILED", "NO_RESULT"):
        return "ERROR"
    if status in ("EXECUTED", "SIMULATED", "ACKED"):
        return "SUCCESS"
    if status == "TIMEOUT":
        return "ERROR"
    if status in ("NOT_DISPATCHED", None):
        return "WAITING"
    return "IDLE"
