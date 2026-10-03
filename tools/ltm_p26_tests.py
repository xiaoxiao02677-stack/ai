#!/usr/bin/env python
"""P26: V1 Core stability test suite.

Covers (spec §五-§十五):
  A. startup/shutdown (repeated restarts, no zombies/duplicates)
  B. command lifecycle integrity (unknown/late/duplicate ACK)
  C. ASR failure recovery (no fabricated utterances, next request clean)
  D. TTS failure recovery (no action replay, lifecycle honest)
  E. ESP32 offline semantics (send=0, honest status)
  F. fault isolation (each failure local)
  G. security scan (no new bypass surface)
  H. workshop state consistency

The long-run soak is a separate script (soak test, >=30min).

Run: uv run python tools/ltm_p26_tests.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_P26_SRC", os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.abspath(SRC))

API = "http://127.0.0.1:12395"
D = "xiaozhi-14c19fd13348"
ok = fail = 0
errors = []


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  OK   {name}")
    else:
        fail += 1
        errors.append(name)
        print(f"  FAIL {name} {detail}")


def section(t):
    print(f"\n== {t} ==")


def get(path):
    with urllib.request.urlopen(API + path, timeout=15) as r:
        return json.loads(r.read())


# ---------------------------------------------------------------------------
section("A. startup/shutdown: restart x3 without residue")
listener_counts = []
for round_no in range(3):
    # restart through the owner-safe script (SIGTERM path)
    subprocess.run(["bash", os.path.join(ROOT, "config",
                                         "restart_server.sh")],
                   capture_output=True, timeout=120)
    for _ in range(45):
        try:
            get("/cc/api/overview")
            break
        except Exception:
            time.sleep(2)
    time.sleep(2)
    # count listeners/processes (no duplicates)
    out = subprocess.run(
        ["bash", "-c", "ss -tln | grep -c ':3333 '"],
        capture_output=True, text=True, timeout=10,
        cwd=ROOT).stdout.strip()
    listener_counts.append(out)
    procs = subprocess.run(
        ["bash", "-c", "pgrep -fc 'run_server.py'"],
        capture_output=True, text=True, timeout=10,
        cwd=ROOT).stdout.strip()
    check(f"A. restart #{round_no+1}: exactly 1 backend + 1 :3333 "
          f"listener",
          out == "1" and procs in ("1", "2"),
          f"listeners={out} procs={procs}")

check("A. all 3 restarts had single listener", all(
    c == "1" for c in listener_counts), listener_counts)

# ---------------------------------------------------------------------------
section("B. command lifecycle integrity (in-process observer)")
from open_llm_vtuber.device_protocol.observer import (  # noqa: E402
    DeviceObserver, LIFECYCLE_TIMEOUT, LIFECYCLE_ACKED)
from open_llm_vtuber.device_protocol.ack import DeviceAck  # noqa: E402
from open_llm_vtuber.device_protocol.session import DeviceRegistry  # noqa

obs = DeviceObserver(registry=DeviceRegistry())
obs.observe_command_created("c1", D, "SET_LED")
obs.observe_command_sent("c1")
obs.observe_terminal("c1", LIFECYCLE_TIMEOUT)
rec = obs.history.get("c1")
check("B. TIMEOUT recorded as terminal", rec.status == LIFECYCLE_TIMEOUT)
# late ACK after TIMEOUT: never re-completes, flag set
r = obs.observe_ack(DeviceAck(command_id="c1", device_id=D,
                              status="ACK"))
rec2 = obs.history.get("c1")
check("B. late ACK after TIMEOUT: stays TIMEOUT, late_ack=True",
      r is False and rec2.status == LIFECYCLE_TIMEOUT
      and rec2.late_ack is True)
# duplicate ACK on ACKED: no rewrite
obs.observe_command_created("c2", D, "SET_LED")
obs.observe_command_sent("c2")
obs.observe_ack(DeviceAck(command_id="c2", device_id=D,
                          status="ACK"))
r = obs.observe_ack(DeviceAck(command_id="c2", device_id=D,
                              status="ACK"))
rec3 = obs.history.get("c2")
check("B. duplicate ACK on ACKED: ignored, history unchanged",
      r is False and rec3.status == LIFECYCLE_ACKED
      and rec3.late_ack is False)
# unknown ack: ignored, no history entry
before = len(obs.history)
r = obs.observe_ack(DeviceAck(command_id="ghost-cmd", device_id=D,
                              status="ACK"))
check("B. unknown ACK: ignored, no fabricated entry",
      r is False and len(obs.history) == before)
# wrong device: ignored
obs.observe_command_created("c3", D, "SET_LED")
obs.observe_command_sent("c3")
r = obs.observe_ack(DeviceAck(command_id="c3", device_id="other-dev",
                              status="ACK"))
rec4 = obs.history.get("c3")
check("B. wrong-device ACK: ignored (forgery guard)",
      r is False and rec4.status not in (LIFECYCLE_ACKED,))
# history bounded FIFO
for i in range(200):
    obs.observe_command_created(f"fill-{i}", D, "SET_LED")
check("B. history bounded (<=64, FIFO eviction)",
      len(obs.history) <= 64)

# ---------------------------------------------------------------------------
section("C. ASR failure recovery")
from open_llm_vtuber.input.schemas import (  # noqa: E402
    ASRProvider, ASRResult)
from open_llm_vtuber.input.gateway import (  # noqa: E402
    ASRAdapter, AudioInputGateway)


class FlakyASR(ASRProvider):
    name = "flaky"

    def __init__(self):
        self.calls = 0
        self.fail_next = True

    def transcribe(self, audio):
        self.calls += 1
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("engine down")
        return "把灯打开。"


gw = AudioInputGateway(adapter=ASRAdapter(provider=FlakyASR()))
audio = [0.0] * 16000
u1 = gw.ingest(audio)          # first call fails
check("C. ASR failure -> NO utterance", u1 is None
      and gw.last_result["status"] == "FAILED")
u2 = gw.ingest(audio)          # second call succeeds
check("C. next request recovers cleanly",
      u2 is not None and u2["text"] == "把灯打开。"
      and u2["source"] == "VOICE")
check("C. failure never fabricates (non-SUCCESS carries no text)",
      gw.adapter.history[0]["transcript"] == "")

# ---------------------------------------------------------------------------
section("D. TTS failure recovery (no action replay)")
from open_llm_vtuber.speech.schemas import (  # noqa: E402
    TTSProvider, AudioPlayer, SpeechResult)
from open_llm_vtuber.speech.gateway import (  # noqa: E402
    RealSpeechAdapter, SpeechGateway)
from open_llm_vtuber.response.schemas import (  # noqa: E402
    SpeechIntent, ResponseIntent)
from open_llm_vtuber.response.coordinator import (  # noqa: E402
    ResponseCoordinator)
from open_llm_vtuber.expression.gateway import (  # noqa: E402
    ExpressionGateway, LCDExpressionAdapter)
from open_llm_vtuber.expression.schemas import ExpressionPolicy  # noqa


class FlakyTTS(TTSProvider):
    name = "flaky_tts"

    def __init__(self):
        self.fail_next = True
        self.calls = 0

    async def synthesize(self, text, timeout=20.0):
        self.calls += 1
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("tts down")
        return "/tmp/p26-ok.mp3"


class NoopPlayer(AudioPlayer):
    name = "noop"

    def play(self, path, timeout=60.0):
        pass


action_calls = []


def action_runner(um, conf_uid, **kw):
    action_calls.append(um)
    return {"execution_status": "SIMULATED",
            "device_ack": {"status": "ACK"}, "command_id": "ac-1"}


coord = ResponseCoordinator(
    speech_adapter=RealSpeechAdapter(provider=FlakyTTS(),
                                     player=NoopPlayer()),
    expression_gateway=ExpressionGateway(
        policy=ExpressionPolicy(),
        adapter=LCDExpressionAdapter(D, mock=True)),
    action_runner=action_runner)
intent = ResponseIntent(speech=SpeechIntent("好的。"),
                        expression_state="EXECUTING",
                        action_request={"operation": "SET_LED",
                                        "parameters": {"on": True}},
                        policy="ACTION_RESPONSE",
                        source_decision_id="d1")
r1 = coord.coordinate(intent, user_message="把灯打开。",
                      conf_uid="p26_d")
check("D. TTS failure -> lifecycle PARTIAL (honest)",
      r1["overall"] == "PARTIAL"
      and r1["results"]["speech"]["status"] == "FAILED")
check("D. action still executed once (no replay, no rollback)",
      len(action_calls) == 1
      and r1["results"]["action"]["device_ack"]["status"] == "ACK")
r2 = coord.coordinate(intent, user_message="把灯打开。",
                      conf_uid="p26_d")
check("D. next TTS recovers (COMPLETED), action runs once more "
      "(new round, no duplication)",
      r2["overall"] == "COMPLETED"
      and r2["results"]["speech"]["status"] == "COMPLETED"
      and len(action_calls) == 2)

# ---------------------------------------------------------------------------
section("E. ESP32 offline semantics (send=0, honest)")
from open_llm_vtuber.execution.adapter import (  # noqa: E402
    ESP32Adapter)
from open_llm_vtuber.device_protocol import DeviceRegistry  # noqa
from open_llm_vtuber.action.schemas import ActionIntentRecord  # noqa


class DeadTransport:
    def send(self, *a, **kw):
        raise RuntimeError("device unreachable")

    def receive(self, *a, **kw):
        raise RuntimeError("device unreachable")


registry = DeviceRegistry()
adapter = ESP32Adapter("capability.led", DeadTransport(), D,
                       session_registry=registry)
# build a minimal intent and drive it through the REAL adapter.run
# (the same path the gateway uses)
intent_rec = ActionIntentRecord.new("p26_e", "p26-dec", "SET_LED")
intent_rec.evaluation_id = "p26-eval"
intent_rec.strategy_id = "p26-strat"
from open_llm_vtuber.capability import get_resolver  # noqa: E402
contract = get_resolver().resolve("SET_LED").capability
from open_llm_vtuber.execution.adapter import ExecutionRequest  # noqa
req = ExecutionRequest(conf_uid="p26_e", intent=intent_rec,
                       contract=contract, policy_status="EXECUTABLE",
                       adapter_id="capability.led")
res = adapter.run(req)
check("E. offline device -> honest FAILED/TIMEOUT (never SUCCESS)",
      res is not None and res.get("status") in
      ("FAILED", "TIMEOUT", "DEVICE_FAILED", "DEVICE_TIMEOUT",
       "DEVICE_GATE_REJECTED"),
      str(res)[:120])

# ---------------------------------------------------------------------------
section("F. fault isolation")
check("F. ASR failure does not touch ESP32 state: no device object "
      "referenced in the input layer",
      "DeviceRegistry" not in open(os.path.join(
          os.path.abspath(SRC), "open_llm_vtuber", "input",
          "gateway.py")).read())
check("F. TTS layer has no reference to execution/action layer",
      "ExecutionGateway" not in open(os.path.join(
          os.path.abspath(SRC), "open_llm_vtuber", "speech",
          "gateway.py")).read())

# ---------------------------------------------------------------------------
section("G. security scan (no new bypass surface)")
from open_llm_vtuber.execution.policy import (  # noqa: E402
    GLOBAL_EXECUTION_ENABLED)
check("G. kill switch still False", GLOBAL_EXECUTION_ENABLED is False)
bad = []
for rel in ("execution", "device_protocol", "server.py"):
    base = os.path.join(os.path.abspath(SRC), "open_llm_vtuber")
    paths = ([os.path.join(base, rel + ".py")]
             if rel.endswith(".py") else
             [os.path.join(base, rel, f) for f in
              os.listdir(os.path.join(base, rel))
              if f.endswith(".py")])
    for p in paths:
        try:
            text = open(p, encoding="utf-8").read()
        except OSError:
            continue
        for marker in ("subprocess.", "os.system", "eval(", "exec("):
            if marker in text:
                bad.append(f"{p}:{marker}")
check("G. no new subprocess/shell/eval/exec in frozen layers",
      not bad, bad[:3])

# ---------------------------------------------------------------------------
section("H. workshop state consistency (live)")
try:
    ov = get("/workshop/api/overview")
    totals = ov.get("totals", {})
    check("H. overview totals coherent (never fabricated)",
          totals.get("devices", 0) >= 0
          and totals.get("online", 0) <= totals.get("devices", 0),
          totals)
    # per-device: the device may be OFFLINE after the restart rounds
    # (the ESP32 reconnects on its own 25s cycle) — an absent device
    # entry is an HONEST state, not a failure
    dev = None
    for entry in ov.get("devices", []):
        if entry.get("device_id") == D:
            dev = entry
            break
    if dev is not None:
        state = dev.get("connection_state")
        check("H. device state is a closed value + online flag "
              "consistent",
              state in ("ONLINE", "STALE", "DISCONNECTED")
              and ("online" not in dev
                   or dev["online"] == (state == "ONLINE")),
              state)
        commands = get(f"/workshop/api/devices/{D}/commands")
        check("H. command history exposed and bounded",
              isinstance(commands.get("commands"), list)
              and len(commands["commands"]) <= 200)
    else:
        # device absent after restarts: wait for the device's own
        # reconnect cycle (observed 60-120s on the real ESP32)
        dev = None
        for _ in range(6):   # up to 120s
            time.sleep(20)
            ov = get("/workshop/api/overview")
            dev = next((e for e in ov.get("devices", [])
                        if e.get("device_id") == D), None)
            if dev is not None:
                break
        check("H. device reappears after its reconnect cycle "
              "(absence was honest, not fabricated)",
              dev is not None and dev.get("connection_state")
              in ("ONLINE", "STALE"),
              "device still absent after 120s wait")
        if dev:
            check("H. reconnected state consistent",
                  ("online" not in dev
                   or dev["online"] == (dev["connection_state"]
                                       == "ONLINE")))
except Exception as e:
    check("H. workshop API reachable", False, str(e))

print(f"\n{'=' * 58}")
print(f"P26 TESTS: {ok} passed, {fail} failed")
print(f"{'=' * 58}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
