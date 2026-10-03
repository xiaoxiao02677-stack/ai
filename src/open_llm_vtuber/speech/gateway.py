"""P24-B SpeechGateway + RealSpeechAdapter — the speech boundary."""

import asyncio
import time
import uuid
from typing import Optional

from .schemas import (SpeechResult, TTSProvider, EdgeTTSProvider,
                      AudioPlayer, LocalFFPlayPlayer, NullAudioPlayer)


class RealSpeechAdapter:
    """Real speech execution: synthesize via a TTSProvider, then play
    via an AudioPlayer. Synthesis and playback are timed SEPARATELY
    (§47) and both failures map to the closed error-code set (§28).

    The adapter never leaks provider/SDK exceptions (§11): everything
    is converted into SpeechResult(error=<closed code>).
    """

    adapter = "real"

    def __init__(self, provider: Optional[TTSProvider] = None,
                 player: Optional[AudioPlayer] = None,
                 synth_timeout: float = 20.0,
                 play_timeout: float = 60.0):
        self.provider = provider or EdgeTTSProvider()
        self.player = player or LocalFFPlayPlayer()
        self.synth_timeout = synth_timeout
        self.play_timeout = play_timeout
        # speech history (bounded; real results only — for the
        # Workshop/response view and tests)
        self.history = []

    def speak(self, intent) -> SpeechResult:
        """Synchronous facade (the P24-A coordinator is sync): runs
        the async synthesis on a private loop. SYNTHESIS vs PLAYBACK
        completion are distinguished (§21)."""
        speech_id = "sp-" + uuid.uuid4().hex[:12]
        text = getattr(intent, "text", "")
        started = time.time()

        # local validation (§33 Test 5/6): empty/invalid text rejected
        if not text or not str(text).strip():
            return SpeechResult(speech_id, "FAILED", text=text,
                                adapter=self.adapter,
                                started_at=started,
                                completed_at=time.time(),
                                error="TTS_TEXT_REJECTED",
                                provider=self.provider.name)

        synth_ms = None
        playback_ms = None
        audio_path = None
        try:
            t0 = time.time()
            audio_path = self._synthesize(text)
            synth_ms = int((time.time() - t0) * 1000)
        except asyncio.TimeoutError:
            return self._fail(speech_id, text, started, "TTS_TIMEOUT",
                              synth_ms=None, audio_path=None)
        except Exception:
            return self._fail(speech_id, text, started,
                              "TTS_SYNTHESIS_ERROR", synth_ms=None,
                              audio_path=None)

        t1 = time.time()
        try:
            self.player.play(audio_path, timeout=self.play_timeout)
            playback_ms = int((time.time() - t1) * 1000)
        except Exception:
            return self._fail(speech_id, text, started,
                              "AUDIO_PLAYBACK_ERROR",
                              synth_ms=synth_ms,
                              audio_path=audio_path)

        result = SpeechResult(
            speech_id, "COMPLETED", text=text,
            adapter=self.adapter, started_at=started,
            completed_at=time.time(), error=None,
            synth_latency_ms=synth_ms,
            playback_latency_ms=playback_ms,
            audio_path=audio_path, provider=self.provider.name)
        self._record(result)
        return result

    async def _synth_with_timeout(self, text: str) -> str:
        """Provider call wrapped in an ADAPTER-LEVEL timeout (§29):
        the timeout is enforced here regardless of whether the
        provider implements its own."""
        return await asyncio.wait_for(
            self.provider.synthesize(text, self.synth_timeout),
            self.synth_timeout + 1.0)

    def _synthesize(self, text: str) -> str:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # running inside an async host: private loop for the
                # blocking facade
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(
                        max_workers=1) as pool:
                    return pool.submit(
                        lambda: asyncio.run(
                            self._synth_with_timeout(text))).result()
        except RuntimeError:
            pass
        return asyncio.run(self._synth_with_timeout(text))

    def _fail(self, speech_id, text, started, code, synth_ms,
              audio_path) -> SpeechResult:
        result = SpeechResult(speech_id, "FAILED", text=text,
                              adapter=self.adapter,
                              started_at=started,
                              completed_at=time.time(), error=code,
                              synth_latency_ms=synth_ms,
                              audio_path=audio_path,
                              provider=self.provider.name)
        self._record(result)
        return result

    def _record(self, result: SpeechResult) -> None:
        self.history.append(dict(result))
        if len(self.history) > 50:
            del self.history[:25]


class SpeechGateway:
    """The ONLY entry into the speech execution layer (§7/§8).

    The coordinator calls gateway.speak(intent); it never touches the
    TTS SDK, HTTP APIs, players, or I2S. Adapters (real or mock) are
    injected — the gateway binds to NEITHER (§34).
    """

    def __init__(self, adapter=None):
        # adapter: anything with .speak(intent) -> result (the P24-A
        # MockSpeechAdapter or the P24-B RealSpeechAdapter)
        self.adapter = adapter

    def speak(self, intent) -> SpeechResult:
        if self.adapter is None:
            return SpeechResult("sp-none", "FAILED",
                                text=getattr(intent, "text", ""),
                                adapter="none",
                                started_at=time.time(),
                                completed_at=time.time(),
                                error="AUDIO_DEVICE_ERROR")
        return self.adapter.speak(intent)
