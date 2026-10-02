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

# ---------------------------------------------------------------------------
# P20 explicit permission source (the Phase-13 "no permission source"
# gap closed MINIMALLY, exactly as the Phase-13 docstring anticipated):
# - P20_GRANT_TABLE: conf_uid-level allowlist for REAL (non-PURE)
#   adapters. Populated ONLY by explicit P20 test/operation code.
# - P20_SWITCH_ON: the in-code switch the REAL path reads. It is NOT
#   the GLOBAL_EXECUTION_ENABLED constant (that stays the documented
#   kill switch) and it is NOT env/config-settable — flipping it
#   requires editing this source (code change + review), same
#   discipline as the global switch. Default OFF.
P20_SWITCH_ON = False
P20_GRANT_TABLE = set()


def is_granted(conf_uid: str, adapter) -> bool:
    """P20 permission source: REAL execution needs BOTH the conf_uid
    grant AND the switch; anything else denies."""
    return P20_SWITCH_ON and conf_uid in P20_GRANT_TABLE

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
        # P20 permission path: the explicit grant table + the P20
        # in-code switch (both default OFF; see P20_GRANT_TABLE above).
        # The GLOBAL kill switch stays the master breaker: OFF -> DENY
        # regardless of grants.
        if not GLOBAL_EXECUTION_ENABLED:
            return PolicyDecision(
                POLICY_DENY,
                "GLOBAL EXECUTION KILL SWITCH is OFF — real execution "
                "denied (sandbox simulation remains available)",
                adapter_id=adapter.adapter_id, side_effect_level=level)
        return PolicyDecision(
            POLICY_DENY,
            "real execution requires the P20 grant table + switch "
            "(default deny; GLOBAL_EXECUTION_ENABLED is not a grant)",
            adapter_id=adapter.adapter_id, side_effect_level=level)

    def decide_p20(self, contract, adapter, conf_uid: str) \
            -> PolicyDecision:
        """P20 REAL path: GLOBAL switch -> P20 grant table -> switch.

        REAL_ALLOWED is reachable ONLY here, and only when ALL of:
        GLOBAL_EXECUTION_ENABLED is True (the master kill switch, a
        code-level constant), conf_uid is in P20_GRANT_TABLE, and
        P20_SWITCH_ON is True (also a code-level constant, default
        OFF). Every other combination denies."""
        base = self.decide(contract, adapter)
        if base.status == POLICY_SANDBOX:
            return base
        if not GLOBAL_EXECUTION_ENABLED or not P20_SWITCH_ON:
            return PolicyDecision(
                POLICY_DENY,
                "P20 real execution denied (kill switch or p20 switch "
                "OFF)",
                adapter_id=getattr(adapter, "adapter_id", ""),
                side_effect_level=str(getattr(adapter,
                                              "side_effect_level", "")))
        if conf_uid not in P20_GRANT_TABLE:
            return PolicyDecision(
                POLICY_DENY,
                f"conf_uid '{conf_uid}' not in the P20 grant table",
                adapter_id=getattr(adapter, "adapter_id", ""),
                side_effect_level=str(getattr(adapter,
                                              "side_effect_level", "")))
        return PolicyDecision(
            POLICY_REAL_ALLOWED,
            "P20 grant table + switches ON — real execution allowed",
            adapter_id=getattr(adapter, "adapter_id", ""),
            side_effect_level=str(getattr(adapter,
                                          "side_effect_level", "")))
