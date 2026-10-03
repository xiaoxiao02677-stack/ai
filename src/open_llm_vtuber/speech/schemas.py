"""P24-B speech domain: SpeechResult + provider/player boundaries."""

import time
from typing import Optional

# closed status set (§10): channel-internal lifecycle states
SPEECH_STATUSES = ("CREATED", "SYNTHESIZING", "READY", "PLAYING",
                   "COMPLETED", "FAILED")

# closed error-code set (§28): converted at the adapter boundary —
# provider SDK exceptions NEVER leak upward
SPEECH_ERROR_CODES = (
    "TTS_SYNTHESIS_ERROR",     # provider failed to synthesize
    "TTS_TIMEOUT",             # synthesis exceeded the timeout
    "TTS_TEXT_REJECTED",       # empty/invalid text rejected locally
    "AUDIO_DEVICE_ERROR",      # no usable output device
    "AUDIO_PLAYBACK_ERROR",    # player failed mid-playback
)


class SpeechResult(dict):
    """Result of one real speech execution (§10/§11).

    Upper layers see THIS object — never pyaudio/ffmpeg/edge-tts
    internals (converted to the closed error-code set above).
    """

    def __init__(self, speech_id: str, status: str,
                 text: str = "", adapter: str = "real",
                 started_at: float = 0.0, completed_at: float = 0.0,
                 error: Optional[str] = None,
                 synth_latency_ms: Optional[int] = None,
                 playback_latency_ms: Optional[int] = None,
                 audio_path: Optional[str] = None,
                 provider: Optional[str] = None):
        if status not in SPEECH_STATUSES:
            raise ValueError(f"unknown speech status '{status}' "
                             f"(allowed: {SPEECH_STATUSES})")
        if error is not None and error not in SPEECH_ERROR_CODES:
            raise ValueError(f"unknown speech error code '{error}' "
                             f"(allowed: {SPEECH_ERROR_CODES})")
        super().__init__({
            "speech_id": speech_id,
            "status": status,
            "text": (text or "")[:200],
            "adapter": adapter,
            "started_at": started_at,
            "completed_at": completed_at,
            "error": error,
            "synth_latency_ms": synth_latency_ms,
            "playback_latency_ms": playback_latency_ms,
            "audio_path": audio_path,
            "provider": provider,
        })


# ---------------------------------------------------------------------------
# TTSProvider boundary (§13): the gateway/adapter never bind a concrete
# TTS API. Default implementation wraps the project's EXISTING edge_tts
# engine (production-proven; no keys, no new service).
# ---------------------------------------------------------------------------
class TTSProvider:
    """Boundary: text -> audio file path (async synthesis)."""

    name = "abstract"

    async def synthesize(self, text: str,
                         timeout: float = 20.0) -> str:
        raise NotImplementedError


class EdgeTTSProvider(TTSProvider):
    """Wraps the project's existing edge_tts engine (src tts package).

    Writes into the project cache/ directory like every other TTS
    path in the codebase; returns the audio file path.
    """

    name = "edge_tts"

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural"):
        self.voice = voice
        self._engine = None

    def _ensure_engine(self):
        if self._engine is None:
            import sys
            import os
            src = os.path.join(os.getcwd(), "src")
            if src not in sys.path:
                sys.path.insert(0, src)
            try:
                from src.open_llm_vtuber.tts.edge_tts import \
                    TTSEngine
            except ImportError:
                from open_llm_vtuber.tts.edge_tts import (  # type: ignore
                    TTSEngine)
            self._engine = TTSEngine(voice=self.voice)
        return self._engine

    async def synthesize(self, text: str,
                         timeout: float = 20.0) -> str:
        import asyncio
        engine = self._ensure_engine()
        import uuid as _uuid
        file_name = "p24b_" + _uuid.uuid4().hex[:12]
        return await asyncio.wait_for(
            engine.async_generate_audio(text, file_name), timeout)


# ---------------------------------------------------------------------------
# AudioPlayer boundary (§16 mode A): local playback on the server's
# real sound card via ffplay (subprocess, bounded, no shell).
# ---------------------------------------------------------------------------
class AudioPlayer:
    """Boundary: play an audio file on the real output device."""

    name = "abstract"

    def play(self, audio_path: str, timeout: float = 60.0) -> None:
        raise NotImplementedError


class LocalFFPlayPlayer(AudioPlayer):
    """Plays via ffplay (SDL) on the default sound card. HEADLESS
    safe (no X); fails closed with a closed error code upstream."""

    name = "ffplay_local"

    def __init__(self, device: Optional[str] = None):
        self.device = device   # optional ALSA device override

    def play(self, audio_path: str, timeout: float = 60.0) -> None:
        import subprocess
        import os
        if not audio_path or not os.path.isfile(audio_path):
            raise RuntimeError("audio file missing: %r" % audio_path)
        env = dict(os.environ)
        env.setdefault("SDL_AUDIODRIVER", "alsa")
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel",
               "quiet", audio_path]
        proc = subprocess.run(cmd, capture_output=True, env=env,
                              timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(
                "ffplay failed rc=%d" % proc.returncode)


class NullAudioPlayer(AudioPlayer):
    """Deterministic no-output player (tests / no-device hosts).
    Clearly named NULL — never a silent stand-in for real playback
    in production E2E (§30 discipline)."""

    name = "null"

    def play(self, audio_path: str, timeout: float = 60.0) -> None:
        return None
