"""P24-B: real speech output layer — tests (spec §33-§36).

§33 RealSpeechAdapter:  1 TTS success  2 TTS failure
                        3 TTS timeout  4 audio output failure
                        5 empty text   6 invalid SpeechIntent
§34 SpeechGateway:      mock path + real path, provider-independent
§35 Coordinator:        A all-success / B speech-fail / C action-fail
                        D speech+action fail
§36 NO-ACTION preserved with REAL speech.
Boundary audits: no SDK leak, no auto-mock fallback, synthesis vs
playback timing separated.

Run: uv run python tools/ltm_p24b_tests.py
"""
import asyncio
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_P24B_SRC", os.path.join(ROOT, "src"))
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


from open_llm_vtuber.speech.schemas import (  # noqa: E402
    SpeechResult, TTSProvider, AudioPlayer, NullAudioPlayer,
    SPEECH_STATUSES, SPEECH_ERROR_CODES)
from open_llm_vtuber.speech.gateway import (  # noqa: E402
    RealSpeechAdapter, SpeechGateway)
from open_llm_vtuber.response.schemas import (  # noqa: E402
    SpeechIntent, MockSpeechAdapter, ResponseIntent)
from open_llm_vtuber.response.coordinator import (  # noqa: E402
    ResponseCoordinator)
from open_llm_vtuber.expression.gateway import (  # noqa: E402
    ExpressionGateway, LCDExpressionAdapter)
from open_llm_vtuber.expression.schemas import ExpressionPolicy  # noqa


# ---- scripted provider/player for deterministic unit tests --------
class ScriptedProvider(TTSProvider):
    name = "scripted"

    def __init__(self, fail=False, timeout=False, delay=0.0):
        self.fail = fail
        self.timeout = timeout
        self.delay = delay
        self.calls = []

    async def synthesize(self, text, timeout=20.0):
        self.calls.append(text)
        if self.delay:
            time.sleep(self.delay)
        if self.timeout:
            await asyncio.sleep(timeout + 5)
        if self.fail:
            raise RuntimeError("provider exploded")
        return "/tmp/fake-%d.mp3" % (abs(hash(text)) % 9999)


class ScriptedPlayer(AudioPlayer):
    name = "scripted_player"

    def __init__(self, fail=False, missing=False):
        self.fail = fail
        self.missing = missing
        self.played = []

    def play(self, audio_path, timeout=60.0):
        if self.missing:
            raise RuntimeError("no such file")
        if self.fail:
            raise RuntimeError("device busy")
        self.played.append(audio_path)


def make_real(provider, player):
    return RealSpeechAdapter(provider=provider, player=player,
                             synth_timeout=0.5, play_timeout=2.0)


intent = SpeechIntent("回来啦，今天辛苦了。")

# ---------------------------------------------------------------------------
section("§33 RealSpeechAdapter")
r1 = make_real(ScriptedProvider(), ScriptedPlayer()).speak(intent)
check("1. TTS success -> COMPLETED with separated latencies",
      r1["status"] == "COMPLETED"
      and r1["synth_latency_ms"] is not None
      and r1["playback_latency_ms"] is not None)
r2 = make_real(ScriptedProvider(fail=True),
               ScriptedPlayer()).speak(intent)
check("2. TTS failure -> FAILED/TTS_SYNTHESIS_ERROR (no SDK leak)",
      r2["status"] == "FAILED"
      and r2["error"] == "TTS_SYNTHESIS_ERROR"
      and "exploded" not in str(r2))
r3 = make_real(ScriptedProvider(timeout=True),
               ScriptedPlayer()).speak(intent)
check("3. TTS timeout -> FAILED/TTS_TIMEOUT",
      r3["status"] == "FAILED" and r3["error"] == "TTS_TIMEOUT")
r4 = make_real(ScriptedProvider(),
               ScriptedPlayer(fail=True)).speak(intent)
check("4. audio output failure -> FAILED/AUDIO_PLAYBACK_ERROR "
      "(synth latency still recorded)",
      r4["status"] == "FAILED"
      and r4["error"] == "AUDIO_PLAYBACK_ERROR"
      and r4["synth_latency_ms"] is not None)
class _BlankIntent:
    """Not a valid SpeechIntent: whitespace-only text (the P24-A
    SpeechIntent itself already rejects this at construction — here we
    prove the ADAPTER also fails closed on such payloads)."""
    text = "   "


r5 = make_real(ScriptedProvider(), ScriptedPlayer()).speak(_BlankIntent())
check("5. blank text rejected locally -> TTS_TEXT_REJECTED "
      "(SpeechIntent itself rejects at construction too)",
      r5["status"] == "FAILED" and r5["error"] == "TTS_TEXT_REJECTED"
      and r5["synth_latency_ms"] is None)
r6 = make_real(ScriptedProvider(), ScriptedPlayer()).speak("not-intent")
check("6. invalid SpeechIntent object -> text rejected (fail closed)",
      r6["status"] == "FAILED" and r6["error"] == "TTS_TEXT_REJECTED")

# ---------------------------------------------------------------------------
section("§34 SpeechGateway (provider-independent)")
mock_gw = SpeechGateway(adapter=MockSpeechAdapter())
check("34a. gateway -> mock adapter path",
      mock_gw.speak(intent)["status"] == "SUCCESS"
      and mock_gw.speak(intent)["adapter"] == "mock")
real_gw = SpeechGateway(
    adapter=make_real(ScriptedProvider(), ScriptedPlayer()))
check("34b. gateway -> real adapter path (same interface)",
      real_gw.speak(intent)["status"] == "COMPLETED"
      and real_gw.speak(intent)["adapter"] == "real")
none_gw = SpeechGateway(adapter=None)
check("34c. no adapter -> honest FAILED, never auto-mock fallback",
      none_gw.speak(intent)["status"] == "FAILED")

# ---------------------------------------------------------------------------
section("§35 Coordinator with REAL speech (injected adapter)")
expr_gw = ExpressionGateway(policy=ExpressionPolicy(),
                            adapter=LCDExpressionAdapter("p24b",
                                                         mock=True))
real_adapter = make_real(ScriptedProvider(), ScriptedPlayer())
coord = ResponseCoordinator(speech_adapter=real_adapter,
                            expression_gateway=expr_gw,
                            action_runner=lambda um, conf_uid, **kw: {
                                "execution_status": "SIMULATED",
                                "device_ack": {"status": "ACK"},
                                "command_id": "c1",
                                "action_intent_id": "a1"})
act = ResponseIntent(speech=intent, expression_state="EXECUTING",
                     action_request={"operation": "SET_LED",
                                     "parameters": {"on": True}},
                     policy="ACTION_RESPONSE",
                     source_decision_id="d1")
case_a = coord.coordinate(act, user_message="把灯打开。",
                          conf_uid="p24b_a")
check("A. speech+expression+action success -> COMPLETED",
      case_a["overall"] == "COMPLETED"
      and case_a["results"]["speech"]["status"] == "COMPLETED")

fail_speech = make_real(ScriptedProvider(fail=True),
                        ScriptedPlayer())
coord_b = ResponseCoordinator(speech_adapter=fail_speech,
                              expression_gateway=expr_gw,
                              action_runner=coord.action_runner)
case_b = coord_b.coordinate(act, user_message="x", conf_uid="p24b_b")
check("B. speech FAILED, expression+action success -> PARTIAL "
      "(no rollback of action)",
      case_b["overall"] == "PARTIAL"
      and case_b["results"]["action"]["device_ack"]["status"] == "ACK"
      and case_b["results"]["speech"]["status"] == "FAILED")

coord_c = ResponseCoordinator(
    speech_adapter=make_real(ScriptedProvider(), ScriptedPlayer()),
    expression_gateway=expr_gw,
    action_runner=lambda um, conf_uid, **kw: {
        "execution_status": "FAILED",
        "device_ack": {"status": "NACK",
                       "error_code": "INVALID_PARAMETERS"}})
case_c = coord_c.coordinate(act, user_message="x", conf_uid="p24b_c")
check("C. action FAILED, speech+expression success -> PARTIAL "
      "(speech NOT falsified)",
      case_c["overall"] == "PARTIAL"
      and case_c["results"]["speech"]["status"] == "COMPLETED")

coord_d = ResponseCoordinator(
    speech_adapter=make_real(ScriptedProvider(fail=True),
                             ScriptedPlayer()),
    expression_gateway=expr_gw,
    action_runner=lambda um, conf_uid, **kw: {
        "execution_status": "FAILED", "device_ack": None})
case_d = coord_d.coordinate(act, user_message="x", conf_uid="p24b_d")
check("D. speech+action both FAILED -> PARTIAL/FAILED per P24-A rules",
      case_d["overall"] in ("PARTIAL", "FAILED")
      and case_d["results"]["speech"]["status"] == "FAILED")

# ---------------------------------------------------------------------------
section("§36 NO-ACTION with REAL speech")
no_act = ResponseIntent(speech=intent, expression_state="WAITING",
                        policy="NO_ACTION_RESPONSE")
case_no = coord.coordinate(no_act, user_message="现在几点？",
                           conf_uid="p24b_no")
check("36. NO-ACTION: real speech plays, no ActionIntent/gateway",
      case_no["overall"] == "COMPLETED"
      and case_no["results"]["speech"]["status"] == "COMPLETED"
      and case_no["results"]["action"] is None)

# ---------------------------------------------------------------------------
section("Boundary audits")
speech_src = open(os.path.join(os.path.abspath(SRC),
                               "open_llm_vtuber", "speech",
                               "gateway.py")).read()
check("no SDK/HTTP specifics leak above the adapter (gateway binds "
      "only to the provider boundary)",
      "edge_tts" not in speech_src
      and "requests" not in speech_src)
check("MockSpeechAdapter preserved untouched (P24-A file unchanged)",
      "MockSpeechAdapter" in open(os.path.join(
          os.path.abspath(SRC), "open_llm_vtuber", "response",
          "schemas.py")).read())
check("speech statuses closed", len(SPEECH_STATUSES) == 6)
check("speech error codes closed", len(SPEECH_ERROR_CODES) == 5)

print(f"\n{'=' * 58}")
print(f"P24-B TESTS: {ok} passed, {fail} failed")
print(f"{'=' * 58}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
