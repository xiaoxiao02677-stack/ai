"""P23 expression runtime wiring (process-level, server side).

Owns the single ExpressionGateway instance with a MOCK LCD adapter
(real LCD hardware is NOT present on the current Xiaozhi build; the
REAL device E2E stays BLOCKED and is never faked). Provides the
event-driven hook: expression_for_run(run_result) maps P21/P22 run
outcomes to expression states through the closed policy — called by
whoever orchestrates turns; observation/memory NEVER drive the LCD
directly (§17).
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if os.path.isdir(os.path.join(_SRC, "open_llm_vtuber")) \
        and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    from src.open_llm_vtuber.expression.schemas import (
        ExpressionIntent, ExpressionPolicy)
    from src.open_llm_vtuber.expression.gateway import (
        ExpressionGateway, LCDExpressionAdapter,
        expression_state_for_run)
except ImportError:
    from open_llm_vtuber.expression.schemas import (  # type: ignore
        ExpressionIntent, ExpressionPolicy)
    from open_llm_vtuber.expression.gateway import (  # type: ignore
        ExpressionGateway, LCDExpressionAdapter,
        expression_state_for_run)

# process-level expression boundary (MOCK LCD; clearly labeled)
expression_gateway = ExpressionGateway(
    policy=ExpressionPolicy(),
    adapter=LCDExpressionAdapter("xiaozhi-14c19fd13348", mock=True))


def expression_for_run(run_result) -> dict:
    """Event-driven hook: map a run outcome to a body expression.

    Independent reliability domain: expression failures are returned,
    never raised — the caller's action result is untouched.
    """
    state = expression_state_for_run(run_result)
    intent = ExpressionIntent(state=state, reason="run outcome",
                              source="system_event")
    return expression_gateway.express(intent)
