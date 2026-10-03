"""P25: real speech input layer — tests (spec §42).

Unit:       UserUtterance / ASRResult / ASRAdapter / InputGateway
Failure:    timeout / failure / empty / invalid / provider error
Integration: audio -> ASR -> UserUtterance
Decision:    UserUtterance -> existing P21 chain (voice & text paths
            converge; Decision source-agnostic)
E2E:        Voice->Text / Voice->Decision->Speech / Action / Expression
Boundaries: input vs output independence; no fabrication; closed sets.

Run: uv run python tools/ltm_p25_tests.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_P25_SRC", os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.abspath(SRC))

ok = fail = 0
errors = []


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  OK   {name}")
    else:
        fail += 1
        errors.append(f"{name} {detail}")
        print(f"  FAIL {name} {detail}")


def section(t):
    print(f"\n== {t} ==")


try:
    import numpy as np  # noqa: E402
except ImportError:  # minimal local fallback: duck-typed arrays
    class _FakeArr:
        def __init__(self, size):
            self.size = size
        @property
        def ndim(self):
            return 1
    def zeros(n, dtype=None):
        return _FakeArr(n)
    np = type("np", (), {"zeros": staticmethod(zeros),
                         "asarray": staticmethod(lambda x, dtype=None:
                                                 x)})()

from open_llm_vtuber.input.schemas import (  # noqa: E402
    ASRResult, UserUtterance, ASRProvider, ASR_STATUSES,
    ASR_ERROR_CODES)
from open_llm_vtuber.input.gateway import (  # noqa: E402
    ASRAdapter, AudioInputGateway)

# ---- scripted provider for deterministic unit tests ----------------
class ScriptedASR(ASRProvider):
    name = "scripted"

    def __init__(self, text="把灯打开。", fail=False, empty=False,
                 hang=False):
        self.text, self.fail, self.empty, self.hang = text, fail, \
            empty, hang
        self.calls = []

    def transcribe(self, audio):
        self.calls.append(audio)
        if self.hang:
            time.sleep(10)
        if self.fail:
            raise RuntimeError("engine exploded")
        return "" if self.empty else self.text


def make_adapter(provider, timeout=1.0):
    return ASRAdapter(provider=provider, timeout=timeout)


# 1 second of silence-shaped float32 @16k
ONE_SEC = np.zeros(16000)

# ---------------------------------------------------------------------------
section("Unit: UserUtterance")
u = UserUtterance("今天有点累。", source="VOICE")
check("VOICE utterance created (id/text/source/asr fields)",
      bool(u["utterance_id"]) and u["text"] == "今天有点累。"
      and u["source"] == "VOICE")
ut = UserUtterance("hello", source="TEXT")
check("TEXT utterance — both sources converge to the same object",
      ut["source"] == "TEXT" and set(ut.keys()) == set(u.keys()))
try:
    UserUtterance("   ", source="TEXT")
    check("blank text rejected (no fabricated utterance)", False)
except ValueError:
    check("blank text rejected", True)
try:
    UserUtterance("x", source="TELEPATHY")
    check("unknown source rejected (closed set)", False)
except ValueError:
    check("unknown source rejected (closed set)", True)

section("Unit: ASRResult closed sets")
check("ASR statuses closed", len(ASR_STATUSES) == 4)
check("ASR error codes closed", len(ASR_ERROR_CODES) == 4)
try:
    ASRResult("HAPPY")
    check("invalid ASR status rejected", False)
except ValueError:
    check("invalid ASR status rejected", True)
try:
    ASRResult("FAILED", transcript="你好")
    check("non-SUCCESS with transcript rejected (no fabrication)",
          False)
except ValueError:
    check("non-SUCCESS with transcript rejected (no fabrication)",
          True)

# ---------------------------------------------------------------------------
section("Unit: ASRAdapter + InputGateway")
r = make_adapter(ScriptedASR()).transcribe(ONE_SEC)
check("SUCCESS result with transcript/provider/latency",
      r["status"] == "SUCCESS" and r["transcript"] == "把灯打开。"
      and r["provider"] == "scripted"
      and r["latency_ms"] is not None
      and r["audio_duration_s"] == 1.0)
gw = AudioInputGateway(adapter=make_adapter(ScriptedASR()))
utt = gw.ingest(ONE_SEC)
check("gateway.ingest -> UserUtterance(source=VOICE, asr attached)",
      utt is not None and utt["source"] == "VOICE"
      and utt["asr"]["provider"] == "scripted")
utt_t = gw.ingest_text("现在几点？")
check("gateway.ingest_text -> UserUtterance(source=TEXT) — unified",
      utt_t is not None and utt_t["source"] == "TEXT")

section("Failure: timeout / failure / empty / invalid")
r_to = make_adapter(ScriptedASR(hang=True), timeout=0.5
                    ).transcribe(ONE_SEC)
check("timeout -> TIMEOUT/ASR_TIMEOUT (adapter-level enforcement)",
      r_to["status"] == "TIMEOUT" and r_to["error"] == "ASR_TIMEOUT")
r_fail = make_adapter(ScriptedASR(fail=True)).transcribe(ONE_SEC)
check("engine failure -> FAILED/ASR_ENGINE_ERROR (no SDK leak)",
      r_fail["status"] == "FAILED"
      and r_fail["error"] == "ASR_ENGINE_ERROR"
      and "exploded" not in str(r_fail))
r_empty = make_adapter(ScriptedASR(empty=True)).transcribe(ONE_SEC)
check("empty engine output -> EMPTY/ASR_NO_SPEECH",
      r_empty["status"] == "EMPTY"
      and r_empty["error"] == "ASR_NO_SPEECH"
      and r_empty["transcript"] == "")
check("empty ASR produces NO utterance (gateway returns None)",
      AudioInputGateway(
          adapter=make_adapter(ScriptedASR(empty=True))
      ).ingest(ONE_SEC) is None)
r_short = make_adapter(ScriptedASR()).transcribe(
    np.zeros(100))
check("too-short audio -> FAILED/ASR_INPUT_INVALID",
      r_short["status"] == "FAILED"
      and r_short["error"] == "ASR_INPUT_INVALID")
r_bad = make_adapter(ScriptedASR()).transcribe("not audio")
check("non-audio input -> FAILED/ASR_INPUT_INVALID (fail closed)",
      r_bad["status"] == "FAILED"
      and r_bad["error"] == "ASR_INPUT_INVALID")

# ---------------------------------------------------------------------------
section("Integration: voice path -> existing Decision (P21)")
import importlib.util as _iu  # noqa: E402
_p21_path = os.path.join(HERE, "..", "p21_runtime.py")
if not os.path.exists(_p21_path):
    _p21_path = os.path.join(HERE, "..", "src_config_p21_runtime.py")
_spec = _iu.spec_from_file_location("p21_runtime", _p21_path)
p21 = _iu.module_from_spec(_spec)
_spec.loader.exec_module(p21)

# voice-derived utterance feeds the SAME handle_user_event as text
run_voice = p21.handle_user_event(utt["text"], conf_uid="p25_t1")
check("voice path: utterance.text -> existing Decision chain "
      "(milestone intent recognized)",
      run_voice.get("no_action") is False
      and run_voice.get("action_intent_id") is not None)
run_text = p21.handle_user_event("我今天终于把项目做完了。",
                                 conf_uid="p25_t2")
check("text path: same chain (convergence — Decision is "
      "source-agnostic)",
      run_text.get("no_action") is False)
run_chat = p21.handle_user_event("现在几点？", conf_uid="p25_t3")
check("pure-chat voice text -> NO-ACTION (no forced action, §31 "
      "Case 1 semantics)",
      run_chat.get("no_action") is True
      and run_chat.get("action_intent_id") is None)

# ---------------------------------------------------------------------------
section("E2E: voice -> decision -> response channels (software)")
from open_llm_vtuber.response.schemas import (  # noqa: E402
    SpeechIntent, ResponseIntent)
from open_llm_vtuber.response.coordinator import (  # noqa: E402
    ResponseCoordinator)
from open_llm_vtuber.expression.gateway import (  # noqa: E402
    ExpressionGateway, LCDExpressionAdapter)
from open_llm_vtuber.expression.schemas import ExpressionPolicy  # noqa
from open_llm_vtuber.speech.gateway import (  # noqa: E402
    SpeechGateway, RealSpeechAdapter)
from open_llm_vtuber.speech.schemas import (  # noqa: E402
    TTSProvider, AudioPlayer)


class _NoopTTS(TTSProvider):
    name = "noop_tts"

    async def synthesize(self, text, timeout=20.0):
        return "/tmp/p25-noop.mp3"


class _NoopPlayer(AudioPlayer):
    name = "noop_player"

    def play(self, path, timeout=60.0):
        pass


coord = ResponseCoordinator(
    speech_adapter=RealSpeechAdapter(provider=_NoopTTS(),
                                     player=_NoopPlayer()),
    expression_gateway=ExpressionGateway(
        policy=ExpressionPolicy(),
        adapter=LCDExpressionAdapter("p25", mock=True)),
    action_runner=lambda um, conf_uid, **kw: {
        "execution_status": "SIMULATED",
        "device_ack": {"status": "ACK"},
        "command_id": "p25-c1", "action_intent_id": "p25-a1"})
resp = coord.coordinate(
    ResponseIntent(speech=SpeechIntent("好的，我帮你打开。"),
                   expression_state="EXECUTING",
                   action_request={"operation": "SET_LED",
                                   "parameters": {"on": True}},
                   policy="ACTION_RESPONSE",
                   source_decision_id="p25-d1"),
    user_message=utt["text"], conf_uid="p25_e2e")
check("voice->decision->speech channel COMPLETED",
      resp["results"]["speech"]["status"] == "COMPLETED")
check("voice->decision->expression channel executed (P23 mock)",
      resp["results"]["expression"]["status"] == "ACKED")
check("voice->decision->action channel executed (P13/P21 path)",
      resp["results"]["action"]["device_ack"]["status"] == "ACK")
check("response lifecycle COMPLETED + observation correlated",
      resp["overall"] == "COMPLETED"
      and resp["observation"]["decision_id"] == "p25-d1")

# ---------------------------------------------------------------------------
section("Boundaries")
gw_in = AudioInputGateway(adapter=make_adapter(ScriptedASR()))
gw_in.ingest(ONE_SEC)   # one real ingest so last_result is populated
check("input/output independence: AudioInputGateway has no reference "
      "to SpeechGateway/TTS",
      "SpeechGateway" not in open(os.path.join(
          os.path.abspath(SRC), "open_llm_vtuber", "input",
          "gateway.py")).read()
      and "TTS" not in open(os.path.join(
          os.path.abspath(SRC), "open_llm_vtuber", "input",
          "gateway.py")).read())
check("input state machine minimal (IDLE/LISTENING/PROCESSING)",
      gw_in.state == "IDLE" and gw_in.listen() == "LISTENING")
check("last ASR result observable (semantic only — no audio blobs)",
      set(gw_in.last_result.keys()) <= {
          "status", "transcript", "provider", "latency_ms",
          "audio_duration_s", "error", "language"})

print(f"\n{'=' * 58}")
print(f"P25 TESTS: {ok} passed, {fail} failed")
print(f"{'=' * 58}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
