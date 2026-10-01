"""CapabilityRegistry: static, process-scoped capability contracts.

Phase 12 design decision (spec §26 recommended): contracts are STATIC
SYSTEM DEFINITIONS — what the system CAN describe — not user
experience data. They do not belong to the Experience/…/ExecutionResult
chain and therefore DO NOT enter StorageProvider (no SQLite/Hermes
tables; no conf_uid scoping — contracts are system-wide).

Built-in contracts mirror the Phase-10 action vocabulary exactly
(RESPOND / REMIND / ACKNOWLEDGE). The registry freezes after
initialization: runtime mutation is refused (register-after-freeze
raises; a test-only unfreeze hook exists solely for audit probes).
Duplicate registration is an ERROR — never a silent overwrite.
"""

import time
from typing import Dict, List, Optional

from .schemas import CapabilityContract

# Phase-10 action vocabulary (single source mirrored here deliberately;
# an import from action.schemas would couple the capability layer to a
# conf-scoped domain — the vocabulary is stable and re-declared locally)
_BUILTIN_ACTION_TYPES = ("RESPOND", "REMIND", "ACKNOWLEDGE")


def _contract(cap_id: str, cap_type: str, description: str,
              input_fields: Dict[str, Dict], output_fields: Dict[str, Dict],
              ) -> CapabilityContract:
    now = time.time()
    c = CapabilityContract(
        capability_id=cap_id,
        capability_type=cap_type,
        version="1",
        description=description,
        input_schema=input_fields,
        output_schema=output_fields,
        allowed_action_types=[cap_type],
        execution_mode="SANDBOX",
        enabled=True,
        metadata={"builtin": True},
        created_at=now,
        updated_at=now,
    )
    c.validate()
    return c


def _builtin_contracts() -> Dict[str, CapabilityContract]:
    return {
        "capability.respond": _contract(
            "capability.respond", "RESPOND",
            "以共情/建议风格回应用户的抽象能力（仅描述，不执行）。",
            {
                "style_hint": {"type": "string", "max_length": 200,
                               "required": True,
                               "description": "回应的语气提示"},
                "condition_hint": {"type": "string", "max_length": 120,
                                   "required": False,
                                   "description": "触发情境提示"},
            },
            {
                "acknowledged_style": {"type": "string", "max_length": 200,
                                       "required": True,
                                       "description": "模拟输出：确认的语气"},
            },
        ),
        "capability.remind": _contract(
            "capability.remind", "REMIND",
            "温和提醒某话题的抽象能力（仅描述，不创建真实提醒）。",
            {
                "topic_hint": {"type": "string", "max_length": 120,
                               "required": True,
                               "description": "提醒话题提示"},
                "condition_hint": {"type": "string", "max_length": 120,
                                   "required": False,
                                   "description": "触发情境提示"},
            },
            {
                "acknowledged_topic": {"type": "string", "max_length": 120,
                                       "required": True,
                                       "description": "模拟输出：确认的话题"},
            },
        ),
        "capability.acknowledge": _contract(
            "capability.acknowledge", "ACKNOWLEDGE",
            "确认/知晓用户表达的抽象能力（仅描述，不修改任何状态）。",
            {
                "condition_hint": {"type": "string", "max_length": 120,
                                   "required": True,
                                   "description": "被确认的情境"},
            },
            {
                "acknowledged": {"type": "string", "max_length": 120,
                                 "required": True,
                                 "description": "模拟输出：确认标记"},
            },
        ),
    }


class CapabilityRegistry:
    """Immutable-after-init registry of static capability contracts."""

    def __init__(self, contracts: Optional[Dict[str, CapabilityContract]] = None):
        self._contracts: Dict[str, CapabilityContract] = dict(
            contracts if contracts is not None else _builtin_contracts())
        self._frozen = True   # frozen from construction (init-only mutation)

    # -- lookup ------------------------------------------------------------------

    def get(self, capability_id: str) -> Optional[CapabilityContract]:
        return self._contracts.get(capability_id)

    def list_ids(self) -> List[str]:
        return sorted(self._contracts.keys())

    def list_contracts(self) -> List[CapabilityContract]:
        return [self._contracts[k] for k in self.list_ids()]

    def __contains__(self, capability_id: str) -> bool:
        return capability_id in self._contracts

    def __len__(self) -> int:
        return len(self._contracts)

    # -- registration (init-phase only) --------------------------------------------

    def register(self, contract: CapabilityContract) -> None:
        """Register at initialization time only. Duplicate ids are an
        ERROR (never a silent overwrite); post-freeze registration is
        refused — runtime business code cannot add capabilities."""
        if self._frozen:
            raise RuntimeError(
                "CapabilityRegistry is frozen — runtime registration "
                "is not allowed (contracts are static system definitions)")
        if contract.capability_id in self._contracts:
            raise ValueError(
                f"duplicate capability_id '{contract.capability_id}' — "
                f"registration rejected")
        contract.validate()
        self._contracts[contract.capability_id] = contract

    # -- test/audit-only hooks -------------------------------------------------------

    def _unfreeze_for_audit(self) -> None:
        """Test/audit probe hook ONLY (leading underscore = private)."""
        self._frozen = False

    def _refreeze(self) -> None:
        self._frozen = True


# module-level singleton: the process-scoped static registry
DEFAULT_REGISTRY = CapabilityRegistry()
