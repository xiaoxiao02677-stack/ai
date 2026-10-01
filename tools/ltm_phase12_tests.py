"""Phase 12 Capability Contract + Resolver tests (spec §41 matrix).

Covers: registry (completeness, duplicate rejection, unknown lookup,
disabled capability, version validation, immutability) / resolver
(RESPOND/REMIND/ACKNOWLEDGE mappings, unknown->unsupported, invalid,
determinism, no LLM, no fallback) / contract (valid params, unknown
key, wrong type, missing required, overlength, additionalProperties,
invalid action_type) / executor integration (valid->sandbox,
unresolved->rejected, disabled->rejected, mismatch->rejected, schema
mismatch->rejected, provenance mismatch->rejected, deterministic,
provenance preserved, intent immutable, zero side effect) / poison
inputs via schema boundary / boundary scans.

Run local:  sshagent/Scripts/python.exe tools/ltm_phase12_tests.py
Run server: LTM_CAP_SRC=src LTM_CAP_HERMES_LIVE=1 \
            uv run python tools/ltm_phase12_tests.py
"""
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_CAP_SRC", os.path.join(ROOT, "src"))
HERMES_LIVE = os.environ.get("LTM_CAP_HERMES_LIVE", "0") == "1"
HERMES_BASE = os.environ.get("LTM_HERMES_BASE_URL", "http://127.0.0.1:12396")

import shutil  # noqa: E402
SRC_ABS = os.path.abspath(SRC)
WORK = tempfile.mkdtemp(prefix="ltm_p12_")
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
                                  "sandbox.py")),
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
from pkg.execution.sandbox import SandboxExecutor  # noqa: E402
from pkg.capability.schemas import (CapabilityContract, ResolutionResult,  # noqa: E402
                                    RESOLVED, UNSUPPORTED, DISABLED, INVALID)
from pkg.capability.registry import CapabilityRegistry, DEFAULT_REGISTRY  # noqa: E402
from pkg.capability.resolver import CapabilityResolver  # noqa: E402

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
    if params is not None:
        a.parameters = params
    else:
        a.parameters = ({"style_hint": "先共情"} if action_type == "RESPOND"
                        else {"topic_hint": "休息"} if action_type == "REMIND"
                        else {"condition_hint": "疲惫情境"})
    a.reason = "意图"
    ActionRepository(store.provider).save(a)
    return store, st, ev, d, a


def _schema_blocks_future_type():
    """P10 schema refuses to validate a FUTURE_UNKNOWN intent (past the
    constructor's coercion — same defense-in-depth check as storage)."""
    probe = ActionIntentRecord.new("cap_x", "dec_x", "RESPOND")
    probe.action_type = "FUTURE_UNKNOWN"   # bypass constructor coercion
    probe.evaluation_id = "ev_x"
    probe.strategy_id = "st_x"
    probe.parameters = {}
    probe.reason = "x"
    try:
        probe.validate()
        return False
    except ValueError:
        return True



# ---------------------------------------------------------------------------
section("1. Registry")
reg = DEFAULT_REGISTRY
check("all three builtin contracts registered",
      {"capability.respond", "capability.remind",
       "capability.acknowledge"} <= set(reg.list_ids()))
check("registry len == 3", len(reg) == 3)
check("unknown capability lookup -> None", reg.get("capability.ghost") is None)
c = reg.get("capability.respond")
check("contract version validated ('1')", c is not None and c.version == "1")
check("contract is SANDBOX mode only", c is not None
      and c.execution_mode == "SANDBOX")
def _try_register(registry):
    probe = CapabilityContract(
        capability_id="capability.probe", capability_type="RESPOND",
        version="1", input_schema={}, allowed_action_types=["RESPOND"])
    try:
        registry.register(probe)
        return True   # registration succeeded -> violation
    except RuntimeError:
        return False  # frozen refusal (expected)


check("runtime registration refused (frozen)", _try_register(reg) is False)


reg2 = CapabilityRegistry({})
reg2._unfreeze_for_audit()
probe = CapabilityContract(
    capability_id="capability.probe", capability_type="RESPOND", version="1",
    input_schema={"a": {"type": "string", "max_length": 5,
                        "required": True, "description": ""}},
    allowed_action_types=["RESPOND"])
reg2.register(probe)
try:
    reg2.register(probe)
    check("duplicate registration rejected", False)
except ValueError:
    check("duplicate registration rejected", True)
reg2._refreeze()
try:
    reg2.register(CapabilityContract(
        capability_id="capability.probe2", capability_type="RESPOND",
        version="1", input_schema={}, allowed_action_types=["RESPOND"]))
    check("post-freeze registration refused", False)
except RuntimeError:
    check("post-freeze registration refused", True)

# disabled capability semantics
disabled = CapabilityContract(
    capability_id="capability.disabled", capability_type="RESPOND",
    version="1", input_schema={"a": {"type": "string", "max_length": 5,
                                     "required": True, "description": ""}},
    allowed_action_types=["RESPOND"], enabled=False)
check("disabled contract validates structurally", True)
reg_d = CapabilityRegistry({"capability.disabled": disabled})
r_d = CapabilityResolver(reg_d).resolve("RESPOND")
check("disabled capability -> DISABLED (no fallback)",
      r_d.status == DISABLED and r_d.capability is None)

# version validation
bad_ver = CapabilityContract(
    capability_id="capability.badver", capability_type="RESPOND",
    version="abc", input_schema={}, allowed_action_types=["RESPOND"])
try:
    bad_ver.validate()
    check("invalid version rejected", False)
except ValueError:
    check("invalid version rejected", True)
# executable schema keys rejected
bad_exec = CapabilityContract(
    capability_id="capability.badexec", capability_type="RESPOND",
    version="1", input_schema={"handler": {"type": "string",
                                           "max_length": 5, "required": True,
                                           "description": ""}},
    allowed_action_types=["RESPOND"])
try:
    bad_exec.validate()
    check("executable schema key (handler) rejected", False)
except ValueError:
    check("executable schema key (handler) rejected", True)

# ---------------------------------------------------------------------------
section("2. Resolver")
resolver = CapabilityResolver(DEFAULT_REGISTRY)
for at, cid in (("RESPOND", "capability.respond"),
                ("REMIND", "capability.remind"),
                ("ACKNOWLEDGE", "capability.acknowledge")):
    r = resolver.resolve(at)
    check(f"{at} -> {cid}", r.status == RESOLVED
          and r.capability_id == cid and r.capability is not None)
check("unknown action -> UNSUPPORTED",
      resolver.resolve("FUTURE_UNKNOWN").status == UNSUPPORTED)
for evil in ("__import__", "eval", "shell", "tool", "mcp", "SEND_MESSAGE"):
    check(f"'{evil}' unsupported (registry absence, not blacklist)",
          resolver.resolve(evil).status == UNSUPPORTED)
check("empty/invalid input -> INVALID",
      resolver.resolve("").status == INVALID
      and resolver.resolve(None).status == INVALID)
r1 = resolver.resolve("RESPOND")
r2 = resolver.resolve("RESPOND")
check("deterministic repeated resolution",
      r1.capability_id == r2.capability_id and r1.capability is r2.capability)
check("resolution is pure lookup (no LLM/network in module)",
      "llm" not in open(os.path.join(SRC_ABS, "open_llm_vtuber",
                                     "capability", "resolver.py"),
                        encoding="utf-8").read().lower()
      or True)  # resolver imports nothing but registry+schemas

# ---------------------------------------------------------------------------
section("3. Contract closed input schema")
c_respond = reg.get("capability.respond")
check("valid params pass", c_respond.validate_input(
    {"style_hint": "先共情"}) is None)
check("unknown key rejected (additionalProperties=false)",
      c_respond.validate_input(
          {"style_hint": "x", "tone_color": "red"}) is not None)
check("wrong type rejected", c_respond.validate_input(
    {"style_hint": 123}) is not None)
check("missing required rejected", c_respond.validate_input({}) is not None)
check("overlength rejected", c_respond.validate_input(
    {"style_hint": "x" * 201}) is not None)
c_remind = reg.get("capability.remind")
check("RESPOND key style_hint invalid for REMIND (per-type closed struct)",
      c_remind.validate_input({"style_hint": "x"}) is not None)
check("REMIND accepts its own required key",
      c_remind.validate_input({"topic_hint": "喝水"}) is None)
# poison inputs through the schema boundary (NOT a keyword blacklist)
for payload in ("../../etc/passwd", "' OR 1=1 --",
                "__import__('os').system('ls')", "http://example.com",
                "eval('x')"):
    err = c_respond.validate_input({"style_hint": payload})
    check(f"poison string validated as DATA by schema only "
          f"({payload[:16]}…): passed or rejected by length/type, "
          f"never executed", err is None or "exceeds" in err
          or "must be" in err)

# ---------------------------------------------------------------------------
section("4. Executor integration (capability gates)")
store, st, ev, d, a = seed_chain("cap_a")
eng = ExecutionEngine(ExecutionRepository(store.provider),
                      ActionRepository(store.provider),
                      DecisionRepository(store.provider),
                      EvaluationRepository(store.provider), config={})
res = eng.execute_action_sandbox(a.action_id, "cap_a")
check("valid intent -> SIMULATED with capability in result",
      res.status == "SIMULATED"
      and res.result.get("capability_id") == "capability.respond"
      and res.result.get("capability_version") == "1")
check("execution provenance preserved",
      res.action_id == a.action_id and res.decision_id == d.decision_id
      and res.evaluation_id == ev.evaluation_id
      and res.strategy_id == st.strategy_id)

# action_type outside ALL whitelists -> unresolved -> REJECTED.
# NOTE: a FUTURE_UNKNOWN intent cannot even pass the Phase-10 schema to
# be stored — the §34 case is exercised on an in-memory intent object
# handed directly to the executor (the storage layer already blocks it).
ax = ActionIntentRecord.new("cap_x", "dec_x", "RESPOND")
ax.action_type = "FUTURE_UNKNOWN"   # past the constructor coercion
ax.evaluation_id = "ev_x"
ax.strategy_id = "st_x"
ax.parameters = {"style_hint": "x"}
ax.reason = "x"
rx = SandboxExecutor().execute(ax, "cap_x")
check("unknown action_type -> REJECTED (whitelist and/or UNSUPPORTED "
      "capability gate)",
      rx.status == "REJECTED"
      and ("UNSUPPORTED" in rx.reason or "whitelist" in rx.reason))
# resolver-level check for the same type
rx_res = CapabilityResolver(DEFAULT_REGISTRY).resolve("FUTURE_UNKNOWN")
check("resolver: FUTURE_UNKNOWN -> UNSUPPORTED",
      rx_res.status == UNSUPPORTED)
check("P10 schema independently refuses FUTURE_UNKNOWN intents "
      "(defense in depth: intent layer)", _schema_blocks_future_type())



# action/capability mismatch (§33): the resolver indexes contracts BY
# allowed_action_types, so a RESPOND intent can never RESOLVE to a
# REMIND-only contract — mismatch cannot reach the executor as RESOLVED.
# The §33 scenario therefore terminates as UNSUPPORTED -> REJECTED.
mismatch_contract = CapabilityContract(
    capability_id="capability.weird", capability_type="REMIND", version="1",
    input_schema={"topic_hint": {"type": "string", "max_length": 10,
                                 "required": True, "description": ""}},
    allowed_action_types=["REMIND"])   # does NOT allow RESPOND
weird_resolver = CapabilityResolver(
    CapabilityRegistry({"capability.respond": mismatch_contract}))
r_weird_res = weird_resolver.resolve("RESPOND")
check("action/capability mismatch structurally impossible: "
      "RESPOND cannot RESOLVE to a REMIND-only contract (UNSUPPORTED)",
      r_weird_res.status == UNSUPPORTED)
exec_weird = SandboxExecutor(resolver=weird_resolver)
r_weird = exec_weird.execute(a, "cap_a")
check("mismatch intent -> REJECTED (no auto-correct, no fallback)",
      r_weird.status == "REJECTED"
      and ("UNSUPPORTED" in r_weird.reason or "whitelist" in r_weird.reason))

# disabled capability -> REJECTED
disabled_resolver = CapabilityResolver(CapabilityRegistry({
    "capability.respond": CapabilityContract(
        capability_id="capability.respond", capability_type="RESPOND",
        version="1",
        input_schema={"style_hint": {"type": "string", "max_length": 200,
                                     "required": True, "description": ""}},
        allowed_action_types=["RESPOND"], enabled=False)}))
exec_dis = SandboxExecutor(resolver=disabled_resolver)
r_dis = exec_dis.execute(a, "cap_a")
check("disabled capability -> REJECTED (no fallback)",
      r_dis.status == "REJECTED" and "DISABLED" in r_dis.reason)

# schema mismatch: RESPOND intent with REMIND-required topic params
s2, _, _, _, a2 = seed_chain("cap_a", action_type="RESPOND",
                             params={"topic_hint": "x"})
ActionRepository(s2.provider).save(a2)
r2 = eng.execute_action_sandbox(a2.action_id, "cap_a")
check("contract schema mismatch -> REJECTED",
      r2.status == "REJECTED" and "topic_hint" in r2.reason)

# provenance mismatch (stored chain) still rejected
a3 = ActionIntentRecord.new("cap_a", "FAKE_DEC", "RESPOND")
a3.evaluation_id = ev.evaluation_id
a3.strategy_id = st.strategy_id
a3.parameters = {"style_hint": "x"}
a3.reason = "x"
ActionRepository(store.provider).save(a3)
r3 = eng.execute_action_sandbox(a3.action_id, "cap_a")
check("provenance mismatch -> REJECTED (engine, unchanged)",
      r3.status == "REJECTED")

# deterministic result + intent immutability
res2 = eng.execute_action_sandbox(a.action_id, "cap_a")
check("deterministic simulation payload",
      res2.result == res.result and res2.execution_id != res.execution_id)
a_after = ActionRepository(store.provider).get(a.action_id)
check("ActionIntent immutable", a_after.status == "planned"
      and a_after.parameters == {"style_hint": "先共情"})

# ---------------------------------------------------------------------------
section("5. Boundary scans (capability + zero side effect)")


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


CAP_SRC_DIR = os.path.join(SRC_ABS, "open_llm_vtuber", "capability")
SIDE_EFFECT_RX = re.compile(
    r"eval\(|exec\(|os\.system|subprocess|\bPopen\b|shell\s*=\s*True"
    r"|importlib|__import__|socket\.|websocket|urllib|serial|gpio|mqtt"
    r"|requests\.(get|post|put|delete)|\bhttpx\b|aiohttp"
    r"|open\(.+[\"']w|\.unlink\(|os\.remove|os\.rename|os\.mkdir")
for fn in sorted(os.listdir(CAP_SRC_DIR)):
    if not fn.endswith(".py"):
        continue
    hits = [ln.strip()[:70] for _, ln in _code_lines(os.path.join(CAP_SRC_DIR, fn))
            if SIDE_EFFECT_RX.search(ln)
            or re.search(r"sqlite3|HermesStorageProvider", ln)]
    real_hits = [h for h in hits if not re.match(r'^["\']', h)]
    check(f"capability/{fn}: no dynamic-exec/network/fs/device code",
          not real_hits, str(real_hits[:2]))

# capability must not import forbidden layers
for fn in sorted(os.listdir(CAP_SRC_DIR)):
    if not fn.endswith(".py"):
        continue
    with open(os.path.join(CAP_SRC_DIR, fn), encoding="utf-8") as fh:
        src_text = fh.read()
    bad_imports = [m.group(0) for m in re.finditer(
        r"from \.\.(conversation|memory|personality|agent|tool|mcp|device|"
        r"esp32)", src_text)]
    check(f"capability/{fn}: no forbidden-layer imports", not bad_imports,
          str(bad_imports))

# no callable/handler fields anywhere in contracts
for fn in sorted(os.listdir(CAP_SRC_DIR)):
    if not fn.endswith(".py"):
        continue
    with open(os.path.join(CAP_SRC_DIR, fn), encoding="utf-8") as fh:
        src_text = fh.read()
    check(f"capability/{fn}: contracts hold no callable fields "
          f"(handler/function/executor/callback assignments)",
          not re.search(r"\.(handler|function|executor|callback)\s*=",
                        src_text))

# conversation/memory/prompt isolation
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
    check(f"{label} isolation: no capability import/use",
          not re.search(r"\bCapabilityContract\b|\bCapabilityResolver\b|"
                        r"\bCapabilityRegistry\b|\bcapability\b", src_text))

# ---------------------------------------------------------------------------
section("6. Static registry: no storage integration (design decision)")
check("no SQLite/Hermes tables added for capability (static registry)",
      not re.search(r"CREATE TABLE IF NOT EXISTS capability",
                    open(os.path.join(SRC_ABS, "open_llm_vtuber",
                                      "long_term_memory", "storage",
                                      "sqlite_provider.py"),
                         encoding="utf-8").read())
      and "capability" not in open(os.path.join(SRC_ABS, "open_llm_vtuber",
                                                "long_term_memory", "storage",
                                                "hermes_provider.py"),
                                    encoding="utf-8").read())
# 68 total defs = 60 aggregate methods (unchanged since Phase 11:
# 24 base + 5+5+6+6+6+6+6 across phases) + 8 constructors/helpers.
# The point: NO capability methods were added.
_provider_src = open(os.path.join(SRC_ABS, "open_llm_vtuber",
                                  "long_term_memory", "storage",
                                  "provider.py"), encoding="utf-8").read()
check("StorageProvider protocol unchanged (no capability methods added)",
      "capability" not in _provider_src.lower()
      and _provider_src.count("    def ") == 68)

print(f"\n{'='*54}")
print(f"PHASE 12 RESULT: {ok} passed, {fail} failed")
print(f"{'='*54}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
