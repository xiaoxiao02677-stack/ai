"""P24-B: real speech output layer (TTS / audio output).

Replaces the P24-A MOCK speech channel with a REAL path, without
touching the sealed P24-A coordination architecture:

    SpeechIntent (unchanged, from P24-A response.schemas)
        -> SpeechGateway (the ONLY entry into the speech layer)
           -> RealSpeechAdapter
              -> TTSProvider boundary (default: the EXISTING
                 edge_tts engine the project already runs in
                 production — no new service, no API keys)
              -> AudioPlayer boundary (default: local ffplay on the
                 server's real sound card)
           -> SpeechResult (closed status set, closed error codes,
              provider details converted at the adapter — never
              leaking SDK exceptions to the coordinator)

SpeechResult distinguishes SYNTHESIS from PLAYBACK (§21/§47): the
adapter records synth_latency_ms and playback_latency_ms separately.

MockSpeechAdapter (P24-A) remains untouched for tests/failure
injection; production never auto-falls-back to it (§30/§32).
"""
