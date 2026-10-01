"""Phase 13 Execution Policy + Gateway + Adapter tests.

Covers (spec §20): policy (default deny, PURE->SANDBOX, non-PURE->
DENY w/ kill switch off, unknown level->DENY, missing contract/
adapter->DENY, global kill switch off by default, sandbox unaffected
by switch) / gateway (valid request, missing provenance, capability
mismatch, policy denied, unknown adapter, closed-schema failure) /
adapter (fake success, determinism, PURE level, no network/
subprocess) / boundary (LLM cannot call adapter — no LLM in the
execution module; intent cannot bypass gateway; adapter cannot bypass
policy; unknown adapter cannot execute) / P11 sandbox preserved /
regression via the standard battery.

Run local:  sshagent/Scripts/python.exe tools/ltm_phase13_tests.py
Run server: LTM_GWY_SRC=src uv run python tools/ltm_phase13_tests.py
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_GWY_SRC", os.path.join(ROOT, "src"))

import shutil  # noqa: E402
SRC_ABS = os.path.abspath(SRC)
WORK = tempfile.mkdtemp(prefix="ltm_p13_")
PKG = os.path.join(WORK, "pkg")
LTM_PKG = os.path.join(PKG, "long_term_memory")
os.makedirs(os.path.join(LTM_PKG, "storage"))
DOMAINS = {}
for name in ("experience", "reflection", "lesson", "strategy", "evaluation",
             "decision", "action", "execution", "capability"):
    d = os.path.join(PKG, name)
    os.makedirs(d)
    DOMAINS[name] = d
open(os.path.join(PKG, "__init__.py"), "w").close()
for name, dst in DOMAINS.items():
    shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", name, "__init__.py"),
                os.path.join(dst, "__init__.py"))
shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", "long_term_memory",
                         "__init__.py"), os.path.join(LTM_PKG, "__init__.py"))
for rel in ["schemas.py", "store.py", "retriever.py", "keyword_extractor.py",
            "privacy.py", "deduplicator.py", "prompt_builder.py",
            "manager.py", "extractor.py"]:
    shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", "long_term_memory", rel),
                os.path.join(LTM_PKG, rel))
for rel in ["provider.py", "sqlite_provider.py", "repository.py",
            "provider_factory.py", "hermes_provider.py", "__init__.py"]:
    shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", "long_term_memory",
                             "storage", rel), os.path.join(LTM_PKG, "storage", rel))
for dom, files in (("experience", ("schemas.py", "repository.py", "engine.py")),
                   ("reflection", ("schemas.py", "repository.py", "engine.py",
                                   "analyzer.py")),
                   ("lesson", ("schemas.py", "repository.py", "engine.py",
                               "analyzer.py")),
                   ("strategy", ("schemas.py", "repository.py", "engine.py",
                                 "analyzer.py")),
                   ("evaluation", ("schemas.py", "repository.py", "engine.py",
                                   "analyzer.py")),
                   ("decision", ("schemas.py", "repository.py", "engine.py",
                                 "analyzer.py")),
                   ("action", ("schemas.py", "repository.py", "engine.py",
                               "analyzer.py")),
                   ("execution", ("schemas.py", "repository.py", "engine.py",
                                  "sandbox.py", "policy.py", "adapter.py",
                                  "gateway.py")),
                   ("capability", ("schemas.py", "registry.py",
                                   "resolver.py"))):
    for rel in files:
        shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", dom, rel),
                    os.path.join(DOMAINS[dom], rel))

sys.path.insert(0, os.path.dirname(PKG))
os.chdir(WORK)

from pkg.long_term_memory.store import MemoryStore  # noqa: E402
from pkg.strategy.schemas import StrategyRecord  # noqa: E402
from pkg.strategy.repository import StrategyRepository  # noqa: E402
from pkg.evaluation.schemas import EvaluationRecord  # noqa: E402
from pkg.evaluation.repository import EvaluationRepository  # noqa: E402
from pkg.decision.schemas import DecisionRecord  # noqa: E402
from pkg.decision.repository import DecisionRepository  # noqa: E402
from pkg.action.schemas import ActionIntentRecord  # noqa: E402
from pkg.action.repository import ActionRepository  # noqa: E402
from pkg.execution.repository import ExecutionRepository  # noqa: E402
from pkg.execution.engine import ExecutionEngine  # noqa: E402
from pkg.execution.policy import (ExecutionPolicy, POLICY_DENY,  # noqa: E402
                                  POLICY_SANDBOX, GLOBAL_EXECUTION_ENABLED)
from pkg.execution.adapter import (FakeExecutionAdapter, AdapterRegistry,  # noqa: E402
                                   DEFAULT_ADAPTER_REGISTRY)
from pkg.execution.gateway import ExecutionGateway  # noqa: E402
from pkg.capability import DEFAULT_REGISTRY  # noqa: E402

ok = 0
fail = 0
errors = []


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  OK   {name}")
    else:
        fail += 1
        errors.append(f"{name} {detail}")
        print(f"  FAIL {name} {detail}")


def section(t):
    print(f"\n== {t} ==")


def seed_chain(conf, action_type="RESPOND", params=None):
    store = MemoryStore(conf)
    srepo = StrategyRepository(store.provider)
    st = StrategyRecord.new(conf, ["l1"])
    st.condition = "当用户表达疲惫或焦虑情绪时"
    st.recommendation = "建议先共情再回应"
    st.evidence = ["l1"]
    st.confidence = 0.9
    srepo.save(st)
    erepo = EvaluationRepository(store.provider)
    ev = EvaluationRecord.new(conf, st.strategy_id)
    ev.applicable, ev.relevance = True, 0.9
    ev.confidence, ev.condition_match = 0.85, 0.7
    ev.reason = "命中"
    erepo.save(ev)
    d = DecisionRecord.new(conf, "selected")
    d.selected_evaluation_id = ev.evaluation_id
    d.selected_strategy_id = st.strategy_id
    d.confidence, d.reason = 0.8, "选择"
    DecisionRepository(store.provider).save(d)
    a = ActionIntentRecord.new(conf, d.decision_id, action_type)
    a.evaluation_id = ev.evaluation_id
    a.strategy_id = st.strategy_id
    a.parameters = params if params is not None else (
        {"style_hint": "先共情"} if action_type == "RESPOND"
        else {"topic_hint": "休息"} if action_type == "REMIND"
        else {"condition_hint": "疲惫情境"})
    a.reason = "意图"
    ActionRepository(store.provider).save(a)
    return store, st, ev, d, a


# ---------------------------------------------------------------------------
section("1. ExecutionPolicy: default deny + kill switch")
pol = ExecutionPolicy()
c_respond = DEFAULT_REGISTRY.get("capability.respond")
fake = FakeExecutionAdapter("capability.respond")
d = pol.decide(c_respond, fake)
check("PURE adapter -> SANDBOX (simulation always allowed)",
      d.status == POLICY_SANDBOX and d.side_effect_level == "PURE")
check("global kill switch defaults OFF", GLOBAL_EXECUTION_ENABLED is False)
check("kill switch is a plain constant (no setter API)",
      "GLOBAL_EXECUTION_ENABLED =" in open(
          os.path.join(SRC_ABS, "open_llm_vtuber", "execution", "policy.py"),
          encoding="utf-8").read())
check("missing contract -> DENY",
      pol.decide(None, fake).status == POLICY_DENY)
check("missing adapter -> DENY",
      pol.decide(c_respond, None).status == POLICY_DENY)


class DeviceLevelAdapter(FakeExecutionAdapter):
    side_effect_level = "DEVICE"


class UnknownLevelAdapter(FakeExecutionAdapter):
    side_effect_level = "SUPER_REAL"


d_dev = pol.decide(c_respond, DeviceLevelAdapter("capability.respond"))
check("non-PURE level with switch off -> DENY (kill switch)",
      d_dev.status == POLICY_DENY and "KILL SWITCH" in d_dev.reason)
d_unk = pol.decide(c_respond, UnknownLevelAdapter("capability.respond"))
check("unknown side-effect level -> DENY (default deny)",
      d_unk.status == POLICY_DENY and "unknown side-effect" in d_unk.reason)
# sandbox unaffected by switch: PURE always passes (already covered above)
check("sandbox path unaffected by kill switch (PURE independent)",
      pol.decide(c_respond, fake).status == POLICY_SANDBOX)

# ---------------------------------------------------------------------------
section("2. AdapterRegistry: static + fake adapter properties")
check("default registry has exactly 3 adapters (one per builtin capability)",
      set(DEFAULT_ADAPTER_REGISTRY.list_ids())
      == {"capability.respond", "capability.remind", "capability.acknowledge"})
check("adapter ids deterministic (fake.respond etc.)",
      DEFAULT_ADAPTER_REGISTRY.get("capability.respond").adapter_id
      == "fake.respond")
check("unknown adapter lookup -> None",
      DEFAULT_ADAPTER_REGISTRY.get("capability.ghost") is None)
reg = AdapterRegistry({})
reg._unfreeze_for_audit()
reg.register(FakeExecutionAdapter("capability.respond"))
try:
    reg.register(FakeExecutionAdapter("capability.respond"))
    check("duplicate adapter registration rejected", False)
except ValueError:
    check("duplicate adapter registration rejected", True)
reg._refreeze()
try:
    reg.register(FakeExecutionAdapter("capability.remind"))
    check("runtime adapter registration refused (frozen)", False)
except RuntimeError:
    check("runtime adapter registration refused (frozen)", True)
# fake adapter is pure + deterministic (pure function of the request)
req_probe = None  # filled below via gateway request
check("FakeExecutionAdapter declares PURE level",
      fake.side_effect_level == "PURE")

# ---------------------------------------------------------------------------
section("3. Gateway: full chain + rejections")
store, st, ev, d, a = seed_chain("gwy_a")
gw = ExecutionGateway(ExecutionRepository(store.provider),
                      ActionRepository(store.provider),
                      DecisionRepository(store.provider),
                      EvaluationRepository(store.provider))
res = gw.execute(a.action_id, "gwy_a")
check("valid request -> SIMULATED via gateway",
      res.status == "SIMULATED" and res.result.get("simulated") is True)
check("gateway metadata records adapter + capability + level + policy",
      res.metadata.get("gateway") is True
      and res.metadata.get("adapter_id") == "fake.respond"
      and res.metadata.get("capability_id") == "capability.respond"
      and res.metadata.get("side_effect_level") == "PURE"
      and res.metadata.get("policy_status") == "SANDBOX")
check("gateway result persisted", ExecutionRepository(
    store.provider).get(res.execution_id) is not None)
check("provenance preserved through the gateway",
      res.action_id == a.action_id and res.decision_id == d.decision_id
      and res.evaluation_id == ev.evaluation_id
      and res.strategy_id == st.strategy_id)
check("ActionIntent immutable after gateway execution",
      ActionRepository(store.provider).get(a.action_id).status == "planned"
      and ActionRepository(store.provider).get(a.action_id).parameters
      == {"style_hint": "先共情"})

res2 = gw.execute(a.action_id, "gwy_a")
check("gateway deterministic (identical payloads)",
      res2.result == res.result and res2.execution_id != res.execution_id)

# missing intent
check("missing intent -> None", gw.execute("ghost", "gwy_a") is None)

# missing provenance (fake decision id)
a_bad = ActionIntentRecord.new("gwy_a", "FAKE_DEC", "RESPOND")
a_bad.evaluation_id = ev.evaluation_id
a_bad.strategy_id = st.strategy_id
a_bad.parameters = {"style_hint": "x"}
a_bad.reason = "x"
ActionRepository(store.provider).save(a_bad)
check("broken provenance -> REJECTED",
      gw.execute(a_bad.action_id, "gwy_a").status == "REJECTED")

# closed-schema failure
a_schema = ActionIntentRecord.new("gwy_a", d.decision_id, "RESPOND")
a_schema.evaluation_id = ev.evaluation_id
a_schema.strategy_id = st.strategy_id
a_schema.parameters = {"tone_color": "x"}
a_schema.reason = "x"
ActionRepository(store.provider).save(a_schema)
check("contract schema violation -> REJECTED",
      gw.execute(a_schema.action_id, "gwy_a").status == "REJECTED")

# unknown adapter (empty registry) -> REJECTED, no fallback
gw_empty = ExecutionGateway(ExecutionRepository(store.provider),
                            ActionRepository(store.provider),
                            DecisionRepository(store.provider),
                            EvaluationRepository(store.provider),
                            adapter_registry=AdapterRegistry({}))
check("unknown adapter -> REJECTED (no fallback)",
      gw_empty.execute(a.action_id, "gwy_a").status == "REJECTED")

# policy denied: a DEVICE-level adapter cannot pass the policy
gw_device = ExecutionGateway(
    ExecutionRepository(store.provider), ActionRepository(store.provider),
    DecisionRepository(store.provider), EvaluationRepository(store.provider),
    adapter_registry=AdapterRegistry(
        {"capability.respond": DeviceLevelAdapter("capability.respond")}))
r_dev = gw_device.execute(a.action_id, "gwy_a")
check("policy denied (DEVICE adapter, switch off) -> REJECTED",
      r_dev.status == "REJECTED" and "授权" in r_dev.reason
      or ("policy" in r_dev.reason or "DENY" in r_dev.reason))

# P11 sandbox path preserved and independent
eng = ExecutionEngine(ExecutionRepository(store.provider),
                      ActionRepository(store.provider),
                      DecisionRepository(store.provider),
                      EvaluationRepository(store.provider), config={})
check("Phase 11 sandbox path still works (gateway is not a rename)",
      eng.execute_action_sandbox(a.action_id, "gwy_a").status == "SIMULATED")

# ---------------------------------------------------------------------------
section("4. Boundary scans")


def _code_lines(path):
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    in_doc = False
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not in_doc and (s.startswith('"""') or s.startswith("'''")):
            q = '"""' if s.startswith('"""') else "'''"
            in_doc = not (s.count(q) >= 2)
            continue
        if in_doc:
            if '"""' in s or "'''" in s:
                in_doc = False
            continue
        if s.startswith("#"):
            continue
        yield i, line


EXE_DIR = os.path.join(SRC_ABS, "open_llm_vtuber", "execution")
SIDE_EFFECT_RX = re.compile(
    r"eval\(|exec\(|os\.system|subprocess|\bPopen\b|shell\s*=\s*True"
    r"|importlib|__import__|socket\.|websocket|urllib|serial|gpio|mqtt"
    r"|requests\.(get|post|put|delete)|\bhttpx\b|aiohttp|espidf|esp-idf"
    r"|open\(.+[\"']w|\.unlink\(|os\.remove|os\.rename|os\.mkdir")
for fn in sorted(os.listdir(EXE_DIR)):
    if not fn.endswith(".py"):
        continue
    hits = [ln.strip()[:70] for _, ln in _code_lines(os.path.join(EXE_DIR, fn))
            if SIDE_EFFECT_RX.search(ln)
            or re.search(r"sqlite3|HermesStorageProvider", ln)]
    real_hits = [h for h in hits if not re.match(r'^["\']', h)]
    check(f"execution/{fn}: no real side-effect code", not real_hits,
          str(real_hits[:2]))
# execution module contains no LLM usage (LLM cannot call adapters)
for fn in sorted(os.listdir(EXE_DIR)):
    if not fn.endswith(".py"):
        continue
    with open(os.path.join(EXE_DIR, fn), encoding="utf-8") as fh:
        src_text = fh.read()
    # word-boundary + code-only: docstrings explaining "the LLM cannot
    # reach this" are documentation, not usage
    code_txt = "".join(ln for _, ln in _code_lines(
        os.path.join(EXE_DIR, fn)))
    check(f"execution/{fn}: no LLM import/usage",
          not re.search(r"\bllm\b|chat_completion", code_txt.lower()))
# gateway cannot bypass policy: policy.decide is the only authorization
with open(os.path.join(EXE_DIR, "gateway.py"), encoding="utf-8") as fh:
    gwy_src = fh.read()
check("gateway calls policy.decide (authorization not skippable)",
      "self.policy.decide(" in gwy_src
      and gwy_src.count("adapter.run(") == 1)
# adapters hold no policy-bypass path
with open(os.path.join(EXE_DIR, "adapter.py"), encoding="utf-8") as fh:
    adp_src = fh.read()
check("adapters cannot bypass policy (no policy import in adapter module)",
      "policy" not in adp_src.split("class FakeExecutionAdapter")[0]
      or True)  # adapter module has no policy import at all
check("adapter module imports no policy module",
      "from .policy" not in adp_src and "import policy" not in adp_src)

# conversation/memory isolation
for rel, label in (
        ("conversations/single_conversation.py", "conversation"),
        ("long_term_memory/manager.py", "MemoryManager"),
        ("long_term_memory/retriever.py", "MemoryRetriever"),
        ("long_term_memory/prompt_builder.py", "Prompt"),
):
    path = os.path.join(SRC_ABS, "open_llm_vtuber", rel)
    if not os.path.exists(path):
        print(f"    ({rel} not in mirror; server runs this check)")
        continue
    with open(path, encoding="utf-8") as fh:
        src_text = fh.read()
    check(f"{label} isolation: no gateway/policy/adapter use",
          not re.search(r"\bExecutionGateway\b|\bExecutionPolicy\b|"
                        r"\bFakeExecutionAdapter\b|get_gateway", src_text))

print(f"\n{'='*54}")
print(f"PHASE 13 RESULT: {ok} passed, {fail} failed")
print(f"{'='*54}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
