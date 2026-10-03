"""P25 input domain: UserUtterance + ASRResult + provider boundary."""

import time
from typing import Optional

# closed ASR status set (§11)
ASR_STATUSES = ("SUCCESS", "EMPTY", "FAILED", "TIMEOUT")

# closed error codes (converted at the adapter — SDK exceptions never
# leak upward)
ASR_ERROR_CODES = (
    "ASR_ENGINE_ERROR",      # engine raised / invalid audio shape
    "ASR_TIMEOUT",           # transcription exceeded the timeout
    "ASR_NO_SPEECH",         # engine returned empty text (VAD empty)
    "ASR_INPUT_INVALID",     # not audio / wrong dtype / too short
)


class ASRResult(dict):
    """Result of one ASR execution (§11): transcript + closed status.
    A FAILED/TIMEOUT/EMPTY result NEVER carries a fabricated
    transcript (§21)."""

    def __init__(self, status: str, transcript: str = "",
                 provider: str = "", latency_ms: Optional[int] = None,
                 audio_duration_s: Optional[float] = None,
                 error: Optional[str] = None,
                 language: Optional[str] = None):
        if status not in ASR_STATUSES:
            raise ValueError(f"unknown ASR status '{status}' "
                             f"(allowed: {ASR_STATUSES})")
        if error is not None and error not in ASR_ERROR_CODES:
            raise ValueError(f"unknown ASR error code '{error}' "
                             f"(allowed: {ASR_ERROR_CODES})")
        if status != "SUCCESS" and transcript:
            raise ValueError("a non-SUCCESS ASR result must not carry "
                             "a transcript (no fabrication)")
        super().__init__({
            "status": status,
            "transcript": transcript or "",
            "provider": provider,
            "latency_ms": latency_ms,
            "audio_duration_s": audio_duration_s,
            "error": error,
            "language": language,
        })


class UserUtterance(dict):
    """One real user input — unified across TEXT and VOICE (§23).

    Decision reads .text; it never knows (or needs to know) whether
    the user typed or spoke. source records the origin for
    observability only.
    """

    def __init__(self, text: str, source: str = "TEXT",
                 utterance_id: str = "", created_at: float = 0.0,
                 asr: Optional[dict] = None):
        if source not in ("TEXT", "VOICE"):
            raise ValueError(f"unknown utterance source '{source}' "
                             f"(allowed: TEXT | VOICE)")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("UserUtterance.text must be a non-empty "
                             "string (no fabricated utterances)")
        if len(text) > 2000:
            raise ValueError("UserUtterance.text too long (max 2000)")
        import uuid as _uuid
        super().__init__({
            "utterance_id": utterance_id
            or ("utt-" + _uuid.uuid4().hex[:12]),
            "text": text,
            "source": source,
            "created_at": created_at or time.time(),
            # asr observability (semantic fields only — never audio,
            # §24/§25)
            "asr": asr,
        })


# ---------------------------------------------------------------------------
# ASRProvider boundary (§12): the gateway/adapter never bind a concrete
# engine. Default wraps the project's EXISTING sherpa_onnx engine.
# ---------------------------------------------------------------------------
class ASRProvider:
    """Boundary: float32 PCM numpy array -> transcript text."""

    name = "abstract"

    def transcribe(self, audio) -> str:
        raise NotImplementedError


class SherpaOnnxProvider(ASRProvider):
    """Wraps the project's existing sherpa_onnx_asr engine
    (VoiceRecognition.transcribe_np) — offline, keyless.

    Model configuration mirrors the production conf.yaml
    (asr_config.sherpa_onnx_asr: model_type=sense_voice with the
    SenseVoice int8 model and tokens shipped under models/)."""

    name = "sherpa_onnx"

    MODEL_DIR = ("models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-"
                 "2024-07-17")

    def __init__(self, model_type: str = "sense_voice",
                 sense_voice: str = None, tokens: str = None,
                 use_itn: bool = True, num_threads: int = 2):
        self._engine = None
        self._kwargs = dict(
            model_type=model_type,
            sense_voice=sense_voice or (
                self.MODEL_DIR + "/model.int8.onnx"),
            tokens=tokens or (self.MODEL_DIR + "/tokens.txt"),
            use_itn=use_itn, num_threads=num_threads)

    def _ensure_engine(self):
        if self._engine is None:
            import sys
            import os
            src = os.path.join(os.getcwd(), "src")
            if src not in sys.path:
                sys.path.insert(0, src)
            try:
                from src.open_llm_vtuber.asr.sherpa_onnx_asr import \
                    VoiceRecognition
            except ImportError:
                from open_llm_vtuber.asr.sherpa_onnx_asr import (  # type: ignore
                    VoiceRecognition)
            self._engine = VoiceRecognition(**self._kwargs)
        return self._engine

    def transcribe(self, audio) -> str:
        engine = self._ensure_engine()
        return engine.transcribe_np(audio)
