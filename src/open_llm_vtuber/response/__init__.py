"""P24-A: Response Coordination Layer — multi-channel response.

Coordinates ONE response across three INDEPENDENT channels:

    ResponseIntent (what this round of response is)
        -> CoordinationPolicy (closed set: how channels combine)
        -> ResponseCoordinator
             |- SpeechIntent    -> MockSpeechAdapter   (mock, labeled)
             |- ExpressionIntent -> P23 ExpressionGateway (reused)
             '- ActionIntent     -> P13/P21 action path   (reused)
        -> channel results (kept SEPARATE, never merged blindly)
        -> ResponseLifecycle (CREATED/PLANNED/RUNNING/COMPLETED/
           PARTIAL/FAILED — distinct from P18 CommandLifecycle)
        -> ResponseObservation (into the existing P22 memory channel)

Boundaries (§4): ResponseIntent != Decision/ActionIntent/
ExpressionIntent; ResponseCoordinator != ExecutionGateway;
ResponseLifecycle != CommandLifecycle. The coordinator NEVER touches
hardware/DeviceCommand directly — it only calls the existing
gateways/runners.
"""
