"""ExecutionPolicy: the authorization boundary between capability and action.

Phase 13 core principle: CAPABILITY != PERMISSION. A capability contract
describes WHAT the system can do; the policy decides WHETHER it may
proceed toward execution — and the answer defaults to NO.

Model (minimal by design — no permission registry, no roles, no UI):
- PURE adapters (deterministic simulation)  -> SANDBOX (always allowed;
  the global kill switch does NOT affect simulation)
- any non-PURE side-effect level            -> DENY in Phase 13:
  the GLOBAL EXECUTION KILL SWITCH is OFF (code-level constant, no
  setter, unreachable from LLM/intents/adapters), and even with the
  switch on there is NO permission source yet — default deny.
  REAL_ALLOWED exists in the model for future phases and is
  deliberately UNREACHABLE today.

Unknown/missing anything (contract, adapter, side-effect level) -> DENY.
"""

from typing import Optional

from ..capability.schemas import CapabilityContract

# ---------------------------------------------------------------------------
# GLOBAL KILL SWITCH — the code-level circuit breaker for real execution.
# Default OFF. There is deliberately NO setter, NO config key, NO env
# override: flipping real execution on requires editing this source file
# (a code change + review), which no LLM, ActionIntent or adapter can do.
# Even if someone flipped it, no non-PURE adapter exists in the static
# registry — defense in depth.
GLOBAL_EXECUTION_ENABLED = False

# side-effect levels (model expressiveness for future adapters; Phase 13
# adapters declare one, the policy reads it — unknown level -> DENY)
SIDE_EFFECT_LEVELS = ("PURE", "LOCAL", "EXTERNAL", "DEVICE", "IRREVERSIBLE")

POLICY_DENY = "DENY"
POLICY_SANDBOX = "SANDBOX"
POLICY_REAL_ALLOWED = "REAL_ALLOWED"   # defined for the model; unreachable in Phase 13


class PolicyDecision:
    """Minimal policy outcome (dataclass-style, kept dependency-free)."""

    def __init__(self, status: str, reason: str,
                 adapter_id: str = "", side_effect_level: str = ""):
        self.status = status
        self.reason = reason
        self.adapter_id = adapter_id
        self.side_effect_level = side_effect_level

    def __repr__(self) -> str:  # pragma: no cover
        return (f"PolicyDecision({self.status}, adapter={self.adapter_id!r}, "
                f"level={self.side_effect_level!r})")


class ExecutionPolicy:
    """Deterministic, default-deny authorization for execution requests."""

    def decide(self, contract: Optional[CapabilityContract],
               adapter) -> PolicyDecision:
        # missing anything -> DENY (never a default allow)
        if contract is None:
            return PolicyDecision(POLICY_DENY, "missing capability contract")
        if adapter is None:
            return PolicyDecision(POLICY_DENY, "missing execution adapter")

        level = getattr(adapter, "side_effect_level", None)
        if level not in SIDE_EFFECT_LEVELS:
            return PolicyDecision(
                POLICY_DENY,
                f"unknown side-effect level '{level}' (allowed: "
                f"{SIDE_EFFECT_LEVELS}) — default deny",
                adapter_id=getattr(adapter, "adapter_id", ""),
                side_effect_level=str(level))

        if level == "PURE":
            # deterministic simulation: unaffected by the global switch
            return PolicyDecision(
                POLICY_SANDBOX,
                "PURE adapter (deterministic simulation) — allowed; the "
                "global kill switch does not apply to simulation",
                adapter_id=adapter.adapter_id, side_effect_level=level)

        # ---- non-PURE levels: real side effects are requested ----
        if not GLOBAL_EXECUTION_ENABLED:
            return PolicyDecision(
                POLICY_DENY,
                "GLOBAL EXECUTION KILL SWITCH is OFF — real execution "
                "denied (sandbox simulation remains available)",
                adapter_id=adapter.adapter_id, side_effect_level=level)
        # Even with the switch on, Phase 13 has NO permission source:
        # default deny. (A future phase may add an explicit grant table;
        # until then REAL_ALLOWED is unreachable.)
        return PolicyDecision(
            POLICY_DENY,
            "no explicit permission source exists for real execution "
            "(default deny)",
            adapter_id=adapter.adapter_id, side_effect_level=level)
