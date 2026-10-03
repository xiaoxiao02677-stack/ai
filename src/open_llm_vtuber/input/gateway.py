"""P25 AudioInputGateway + ASRAdapter — the input boundary."""

import time
import uuid
from typing import Optional

try:
    import numpy as _np
except ImportError:  # hosts without numpy (local dev): duck-typed
    # validation only — duration math still works for array-likes
    class _DuckNP:
        float32 = None   # dtype hint accepted and ignored

        @staticmethod
        def asarray(x, dtype=None):
            return x

        @staticmethod
        def zeros(n, dtype=None):
            class _A:
                ndim = 1
                size = n
            return _A()

    _np = _DuckNP()

from .schemas import (ASRResult, UserUtterance, ASRProvider,
                      SherpaOnnxProvider)

_MIN_AUDIO_SECS = 0.2     # shorter than this = not real speech input
_MAX_AUDIO_SECS = 60.0


class ASRAdapter:
    """Audio -> transcript through a provider, with an ADAPTER-LEVEL
    timeout (never trusting provider internals) and closed error
    conversion (SDK exceptions never leak)."""

    def __init__(self, provider: Optional[ASRProvider] = None,
                 timeout: float = 30.0):
        self.provider = provider or SherpaOnnxProvider()
        self.timeout = timeout
        self.history = []   # bounded ASR results (semantic only)

    def transcribe(self, audio) -> ASRResult:
        t0 = time.time()
        # local input validation (§20): must be a float ndarray of a
        # sane duration (16k mono assumed, same as the /asr endpoint)
        try:
            arr = _np.asarray(audio, dtype=_np.float32)
        except Exception:
            return self._result("FAILED", error="ASR_INPUT_INVALID",
                                t0=t0, audio=None)
        if not hasattr(arr, "ndim") or arr.ndim != 1 \
                or not hasattr(arr, "size") or arr.size == 0:
            return self._result("FAILED", error="ASR_INPUT_INVALID",
                                t0=t0, audio=arr)
        duration = float(arr.size) / 16000.0
        if duration < _MIN_AUDIO_SECS:
            return self._result("FAILED", error="ASR_INPUT_INVALID",
                                t0=t0, audio=arr,
                                duration=duration)
        if duration > _MAX_AUDIO_SECS:
            return self._result("FAILED", error="ASR_INPUT_INVALID",
                                t0=t0, audio=arr,
                                duration=duration)
        try:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=1) as pool:
                text = pool.submit(
                    self._transcribe_with_timeout, arr).result()
        except _ASRTimeout:
            return self._result("TIMEOUT", error="ASR_TIMEOUT", t0=t0,
                                audio=arr, duration=duration)
        except Exception:
            return self._result("FAILED", error="ASR_ENGINE_ERROR",
                                t0=t0, audio=arr, duration=duration)
        text = (text or "").strip()
        if not text:
            # engine heard nothing (silence / noise below VAD) — an
            # HONEST EMPTY, never a fabricated utterance (§21)
            return self._result("EMPTY", error="ASR_NO_SPEECH", t0=t0,
                                audio=arr, duration=duration)
        return self._result("SUCCESS", transcript=text, t0=t0,
                            audio=arr, duration=duration)

    def _transcribe_with_timeout(self, arr):
        import signal   # noqa: F401 — not portable; use thread-based
        # thread-based timeout: run provider in this worker; the outer
        # pool.result(timeout) enforces the deadline
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) \
                as inner:
            fut = inner.submit(self.provider.transcribe, arr)
            try:
                return fut.result(timeout=self.timeout)
            except concurrent.futures.TimeoutError:
                raise _ASRTimeout()

    def _result(self, status, t0, audio=None, duration=None,
                transcript="", error=None) -> ASRResult:
        latency = int((time.time() - t0) * 1000) if t0 else None
        result = ASRResult(status=status, transcript=transcript,
                           provider=self.provider.name,
                           latency_ms=latency,
                           audio_duration_s=duration, error=error)
        self.history.append(dict(result))
        if len(self.history) > 50:
            del self.history[:25]
        return result


class _ASRTimeout(Exception):
    pass


class AudioInputGateway:
    """The ONLY entry into the speech-input layer (§7).

    Upper layers call gateway.ingest(audio) -> UserUtterance | None;
    they never touch ASR SDKs, PCM handling, or engines. The gateway
    also exposes ingest_text() so TEXT and VOICE converge into the
    SAME UserUtterance (§23) — Decision stays source-agnostic.
    """

    def __init__(self, adapter: Optional[ASRAdapter] = None):
        self.adapter = adapter or ASRAdapter()
        self.last_result: Optional[ASRResult] = None
        self.last_utterance: Optional[UserUtterance] = None
        # input state (§38 minimal): IDLE/LISTENING/PROCESSING
        self.state = "IDLE"

    def ingest(self, audio) -> Optional[UserUtterance]:
        """Voice path: audio -> ASR -> UserUtterance (or None — a
        failure NEVER fabricates an utterance, §20/§21)."""
        if self.adapter is None:
            self.last_result = ASRResult("FAILED", provider="none",
                                         error="ASR_ENGINE_ERROR")
            return None
        self.state = "PROCESSING"
        result = self.adapter.transcribe(audio)
        self.last_result = result
        self.state = "IDLE"
        if result["status"] != "SUCCESS":
            return None
        utterance = UserUtterance(text=result["transcript"],
                                  source="VOICE",
                                  asr={k: result[k] for k in
                                       ("status", "provider",
                                        "latency_ms",
                                        "audio_duration_s")})
        self.last_utterance = utterance
        return utterance

    def ingest_text(self, text: str) -> Optional[UserUtterance]:
        """Text path: the SAME UserUtterance with source=TEXT (§23) —
        the two input paths converge; Decision never branches on the
        origin."""
        try:
            utterance = UserUtterance(text=text, source="TEXT")
        except ValueError:
            return None
        self.last_utterance = utterance
        return utterance

    def listen(self):
        self.state = "LISTENING"
        return self.state
