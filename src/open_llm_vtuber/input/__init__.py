"""P25: real speech input layer (ASR / audio input).

Wires REAL user voice into the EXISTING decision chain:

    real audio (text or voice)
        -> AudioInputGateway (the ONLY entry into the input layer)
           -> ASRAdapter
              -> ASRProvider boundary (default: the project's
                 EXISTING sherpa_onnx_asr engine — offline,
                 production-proven, no keys, no cloud)
           -> ASRResult (closed status set; failures never fabricate
              a transcript)
        -> UserUtterance (unified input object: Decision never knows
           whether the user typed or spoke — source=TEXT|VOICE)
        -> the EXISTING P21 decision chain (handle_user_event —
           zero modification)

Boundaries (spec): ASR produces TRANSCRIPT ONLY — no emotion, no
intent, no direct Action/TTS/ESP32 access. Input (AudioInputGateway)
and output (P24 SpeechGateway) are fully independent directions.
"""
