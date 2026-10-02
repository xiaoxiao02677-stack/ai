"""P23: Body Expression infrastructure — AI state -> body expression.

A SEPARATE boundary from Action execution (P13 ExecutionGateway stays
the only ACTION boundary; this package is the EXPRESSION boundary):

    AI internal state (system-event driven)
        -> ExpressionIntent (what the body should express)
        -> ExpressionPolicy (closed state->expression mapping)
        -> ExpressionGateway (the expression boundary)
        -> LCDExpressionAdapter (body expression adapter)
        -> DeviceCommand SET_LCD_STATE (existing P15 protocol,
           reused via the existing session/transport/codec stack)
        -> device ACK (existing P18 CommandLifecycle semantics)

Reuses: p19r_service.XiaozhiCodec, the workshop GatewayChannel, the
P15 DeviceCommand closed schema (SET_LCD_STATE joins DEVICE_OPERATIONS
as a backward-compatible extension: old devices simply never advertise
it, so the P17 device gate keeps refusing it for them; SET_LED and
everything else is untouched).

Expression NEVER bypasses the device session/transport boundaries, and
expression failures NEVER fail core actions (independent reliability
domains).
"""
