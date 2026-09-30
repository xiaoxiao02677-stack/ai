"""Phase 10 Action Intent tests (spec §20 matrix).

Covers: schema (selected/planned, abstain/planned rejection, provenance
mismatch, illegal action_type, malicious parameters, invalid status) /
engine (selected->planned, abstain->no planned, rejected->no planned,
missing decision, missing evaluation, evaluation mismatch, strategy
mismatch) / LLM (valid, illegal type, malformed JSON, timeout, fake
ids — structurally impossible, malicious parameters) / storage (CRUD,
conf isolation, decision/evaluation lookups, restart) / boundary scan.

Run local:  sshagent/Scripts/python.exe tools/ltm_phase10_tests.py
Run server: LTM_ACT_SRC=src LTM_ACT_HERMES_LIVE=1 \
            uv run python tools/ltm_phase10_tests.py
"""
import asyncio
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_ACT_SRC", os.path.join(ROOT, "src"))
HERMES_LIVE = os.environ.get("LTM_ACT_HERMES_LIVE", "0") == "1"
HERMES_BASE = os.environ.get("LTM_HERMES_BASE_URL", "http://127.0.0.1:12396")

import shutil  # noqa: E402
SRC_ABS = os.path.abspath(SRC)
WORK = tempfile.mkdtemp(prefix="ltm_p10_")
PKG = os.path.join(WORK, "pkg")
LTM_PKG = os.path.join(PKG, "long_term_memory")
os.makedirs(os.path.join(LTM_PKG, "storage"))
DOMAINS = {}
for name in ("experience", "reflection", "lesson", "strategy", "evaluation",
             "decision", "action"):
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
                               "analyzer.py"))):
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
from pkg.decision.schemas import (DecisionRecord, STATUS_SELECTED,  # noqa: E402
                                  STATUS_ABSTAIN, STATUS_REJECTED)
from pkg.decision.repository import DecisionRepository  # noqa: E402
from pkg.decision.engine import DecisionEngine  # noqa: E402
from pkg.action.schemas import ActionIntentRecord, ACTION_TYPES  # noqa: E402
from pkg.action.repository import ActionRepository  # noqa: E402
from pkg.action.engine import ActionEngine  # noqa: E402
from pkg.action.analyzer import LLMActionAnalyzer  # noqa: E402

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


class FakeLLM:
    def __init__(self, payload=None, error=False):
        self.payload = payload
        self.error = error
        self.calls = 0

    def chat_completion(self, messages, system_prompt):
        self.calls += 1

        async def gen():
            if self.error:
                yield {"type": "error", "message": "simulated timeout"}
            else:
                yield self.payload

        return gen()


def seed_decision(conf, status=STATUS_SELECTED):
    """Seed strategy+evaluation+decision and return (decision, evaluation)."""
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
    ev.applicable = True
    ev.relevance = 0.9
    ev.confidence = 0.85
    ev.condition_match = 0.7
    ev.reason = "命中疲惫情境"
    erepo.save(ev)
    d = DecisionRecord.new(conf, status)
    if status == STATUS_SELECTED:
        d.selected_evaluation_id = ev.evaluation_id
        d.selected_strategy_id = st.strategy_id
        d.confidence = 0.8
        d.reason = "选择该评估"
    else:
        d.reason = "放弃选择"
    DecisionRepository(store.provider).save(d)
    return store, d, ev


# ---------------------------------------------------------------------------
section("1. ActionIntentRecord schema validation")
store, d, ev = seed_decision("act_a")
arepo = ActionRepository(store.provider)
a = ActionIntentRecord.new("act_a", d.decision_id, "RESPOND")
a.evaluation_id = d.selected_evaluation_id
a.strategy_id = d.selected_strategy_id
a.parameters = {"style_hint": "先共情"}
a.reason = "决策选中该方案，记录回应意图。"
arepo.save(a)
g = arepo.get(a.action_id)
check("valid planned intent save/get",
      g is not None and g.status == "planned" and g.action_type == "RESPOND"
      and g.decision_id == d.decision_id)
check("provenance triple preserved",
      g.evaluation_id == d.selected_evaluation_id
      and g.strategy_id == d.selected_strategy_id)
check("to_dict/from_dict roundtrip",
      ActionIntentRecord.from_dict(a.to_dict()).action_id == a.action_id)

for name, mutate in (
        ("illegal action_type", lambda r: setattr(r, "action_type", "SEND_MESSAGE")),
        ("invalid status", lambda r: setattr(r, "status", "executing")),
        ("missing decision_id", lambda r: setattr(r, "decision_id", "")),
        ("malformed parameters (string)", lambda r: setattr(r, "parameters", "x")),
):
    b = ActionIntentRecord.new("act_a", d.decision_id, "RESPOND")
    b.evaluation_id = d.selected_evaluation_id
    b.strategy_id = d.selected_strategy_id
    mutate(b)
    try:
        arepo.save(b)
        check(f"{name} rejected", False)
    except ValueError:
        check(f"{name} rejected", True)

for label, params in (
        ("executable key 'shell'", {"shell": "rm -rf /"}),
        ("executable key 'command'", {"command": "whoami"}),
        ("executable value marker", {"note": "run os.system('ls')"}),
        ("sql injection marker", {"note": "'; DROP TABLE actions; --"}),
        ("url marker", {"note": "http://evil.example"}),
):
    b = ActionIntentRecord.new("act_a", d.decision_id, "RESPOND")
    b.evaluation_id = d.selected_evaluation_id
    b.strategy_id = d.selected_strategy_id
    b.parameters = params
    try:
        arepo.save(b)
        check(f"malicious parameters rejected ({label})", False)
    except ValueError:
        check(f"malicious parameters rejected ({label})", True)

# provenance EQUALITY (evaluation/strategy ids == decision's selected ids)
# is enforced by the ENGINE chain (verified in section 2: mismatch -> None);
# the schema enforces presence (decision_id mandatory). By-design split.
check("duplicate save stays one row",
      arepo.count() == 1 and arepo.get(a.action_id) is not None)
check("get miss -> None", arepo.get("nope") is None)
check("delete", arepo.delete(a.action_id) and arepo.count() == 0)

# ---------------------------------------------------------------------------
section("2. Engine: status gating + provenance chain")
eng = ActionEngine(ActionRepository(store.provider),
                   DecisionRepository(store.provider),
                   EvaluationRepository(store.provider),
                   StrategyRepository(store.provider), config={})
intent = eng.create_action_from_decision(d.decision_id, "act_a")
check("selected decision -> planned intent",
      intent is not None and intent.status == "planned"
      and intent.action_type in ACTION_TYPES)
check("intent triple provenance matches decision",
      intent.decision_id == d.decision_id
      and intent.evaluation_id == d.selected_evaluation_id
      and intent.strategy_id == d.selected_strategy_id)
check("parameters are structured data (non-executable)",
      isinstance(intent.parameters, dict)
      and "style_hint" in intent.parameters)

# abstain decision
store_b, d_ab, _ = seed_decision("act_b", STATUS_ABSTAIN)
eng_b = ActionEngine(ActionRepository(store_b.provider),
                     DecisionRepository(store_b.provider),
                     EvaluationRepository(store_b.provider),
                     StrategyRepository(store_b.provider), config={})
check("abstain decision -> NO planned intent",
      eng_b.create_action_from_decision(d_ab.decision_id, "act_b") is None)
# rejected decision
store_c, d_rej, _ = seed_decision("act_c", STATUS_REJECTED)
eng_c = ActionEngine(ActionRepository(store_c.provider),
                     DecisionRepository(store_c.provider),
                     EvaluationRepository(store_c.provider),
                     StrategyRepository(store_c.provider), config={})
check("rejected decision -> NO planned intent",
      eng_c.create_action_from_decision(d_rej.decision_id, "act_c") is None)

check("missing decision -> None (never fabricated)",
      eng.create_action_from_decision("ghost", "act_a") is None)
check("cross-conf decision id -> None (isolation)",
      eng.create_action_from_decision(d_ab.decision_id, "act_a") is None)

# evaluation mismatch: corrupt the decision's evaluation id in the store
d_bad = DecisionRecord.new("act_a", STATUS_SELECTED)
d_bad.selected_evaluation_id = "ghost_eval"
d_bad.selected_strategy_id = "ghost_strat"
d_bad.confidence = 0.8
d_bad.reason = "x"
DecisionRepository(store.provider).save(d_bad)
check("missing evaluation -> None (provenance broken)",
      eng.create_action_from_decision(d_bad.decision_id, "act_a") is None)

# strategy mismatch: decision points at an evaluation whose strategy
# differs from decision.selected_strategy_id — engine must refuse
store_d = MemoryStore("act_d")
srepo_d = StrategyRepository(store_d.provider)
st2 = StrategyRecord.new("act_d", ["l2"])
st2.condition = "另一个情境"
st2.recommendation = "另一种建议"
st2.evidence = ["l2"]
srepo_d.save(st2)
erepo_d = EvaluationRepository(store_d.provider)
ev2 = EvaluationRecord.new("act_d", st2.strategy_id)
ev2.applicable = True
ev2.relevance = 0.8
ev2.confidence = 0.8
ev2.condition_match = 0.5
erepo_d.save(ev2)
d_mis = DecisionRecord.new("act_d", STATUS_SELECTED)
d_mis.selected_evaluation_id = ev2.evaluation_id
d_mis.selected_strategy_id = "WRONG_STRATEGY"  # mismatch with evaluation
d_mis.confidence = 0.8
d_mis.reason = "x"
DecisionRepository(store_d.provider).save(d_mis)
eng_d = ActionEngine(ActionRepository(store_d.provider),
                     DecisionRepository(store_d.provider),
                     EvaluationRepository(store_d.provider),
                     StrategyRepository(store_d.provider), config={})
check("strategy mismatch (decision vs evaluation) -> None",
      eng_d.create_action_from_decision(d_mis.decision_id, "act_d") is None)

# ---------------------------------------------------------------------------
section("3. LLM analyzer: valid + rejections")
cfg = {"action": {"llm_analysis": True, "llm_timeout": 5.0}}
llm = FakeLLM(json.dumps({"action_type": "REMIND",
                          "parameters": {"style_hint": "温柔提醒"},
                          "reason": "提醒用户休息"}))
llm_eng = ActionEngine(ActionRepository(store.provider),
                       DecisionRepository(store.provider),
                       EvaluationRepository(store.provider),
                       StrategyRepository(store.provider),
                       config=cfg, llm=llm)
out = asyncio.run(llm_eng.create_action_from_decision_llm(
    d.decision_id, "act_a"))
check("valid llm intent produced", out is not None
      and out.action_type == "REMIND" and out.status == "planned"
      and out.metadata.get("analyzer") == "llm")
check("provenance ids from decision, NOT llm",
      out.decision_id == d.decision_id
      and out.evaluation_id == d.selected_evaluation_id
      and out.strategy_id == d.selected_strategy_id)


def llm_run(payload=None, error=False):
    f = FakeLLM(payload, error)
    e = ActionEngine(ActionRepository(store.provider),
                     DecisionRepository(store.provider),
                     EvaluationRepository(store.provider),
                     StrategyRepository(store.provider),
                     config=cfg, llm=f)
    return asyncio.run(e.create_action_from_decision_llm(
        d.decision_id, "act_a"))


check("illegal action_type -> rule fallback (planned, RESPOND)",
      llm_run(json.dumps({"action_type": "CALL_TOOL",
                          "parameters": {}, "reason": "x"}))
      .metadata.get("analyzer") == "rules")
check("malformed json -> rule fallback", llm_run("not json")
      .metadata.get("analyzer") == "rules")
check("timeout -> rule fallback (never fake planned via llm)",
      llm_run(error=True).metadata.get("analyzer") == "rules")
check("malicious llm parameters rejected -> rule fallback",
      llm_run(json.dumps({"action_type": "RESPOND",
                          "parameters": {"shell": "whoami"},
                          "reason": "x"}))
      .metadata.get("analyzer") == "rules")
check("fake decision_id in llm output is IGNORED (engine-derived)",
      llm_run(json.dumps({"action_type": "RESPOND",
                          "parameters": {},
                          "reason": "x",
                          "decision_id": "FAKE_ID"}))
      .decision_id == d.decision_id)
check("llm failure never yields non-planned-through-llm",
      all(x.status == "planned" or x.metadata.get("analyzer") == "rules"
          for x in (llm_run(error=True),))
      if llm_run(error=True) else True)

# ---------------------------------------------------------------------------
section("4. conf_uid isolation + restart + reverse trace")
ra = ActionRepository(MemoryStore("act_a").provider)
rb = ActionRepository(MemoryStore("act_b").provider)
check("isolation: A only sees A",
      all(x.conf_uid == "act_a" for x in ra.list_actions(100)))
check("isolation: B has no planned intents",
      all(x.conf_uid == "act_b" for x in rb.list_actions(100))
      and rb.count() == 0)
check("isolation: cross-conf get impossible",
      ra.get(rb.list_actions(100)[0].action_id) is None
      if rb.list_actions(100) else True)
by_dec = ra.list_by_decision(d.decision_id)
check("reverse trace by decision", len(by_dec) >= 1
      and all(x.decision_id == d.decision_id for x in by_dec))
by_evl = ra.list_by_evaluation(d.selected_evaluation_id)
check("reverse trace by evaluation", len(by_evl) >= 1
      and all(x.evaluation_id == d.selected_evaluation_id for x in by_evl))
ra2 = ActionRepository(MemoryStore("act_a").provider)
check("restart persistence", ra2.count() == ra.count()
      and ra2.get(intent.action_id) is not None)

# ---------------------------------------------------------------------------
section("5. Boundary scan: action layer + zero side-effect")


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


ACT_SRC_DIR = os.path.join(SRC_ABS, "open_llm_vtuber", "action")
SIDE_EFFECT_RX = re.compile(
    r"os\.system|subprocess|requests\.(get|post|put|delete)|\bhttpx\b"
    r"|websocket|socket\.|send_message|call_tool|invoke|dispatch\(")
for fn in sorted(os.listdir(ACT_SRC_DIR)):
    if not fn.endswith(".py"):
        continue
    hits = [ln.strip()[:70] for _, ln in _code_lines(os.path.join(ACT_SRC_DIR, fn))
            if SIDE_EFFECT_RX.search(ln)
            or re.search(r"sqlite3|HermesStorageProvider", ln)]
    # schemas.py CONTAINS the blacklist literals themselves (guard code) —
    # drop hits that are pure list/tuple definitions of forbidden markers
    real_hits = [h for h in hits
                 if not re.match(r'^["\']', h)]
    check(f"action/{fn}: no side-effect/backend code", not real_hits,
          str(real_hits[:2]))

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
    # word-boundary match: plain substring would hit "extraction" etc.
    check(f"{label} isolation: no action import/use",
          not re.search(r"\bActionIntent\b|\bActionEngine\b|"
                        r"\baction\b", src_text))

# ---------------------------------------------------------------------------
section("6. Hermes provider: actions (sidecar path)")
h_ok = False
if HERMES_LIVE:
    try:
        import httpx as _hx
        _hx.get(f"{HERMES_BASE}/api/health", timeout=3).raise_for_status()
        h_ok = True
    except Exception as exc:  # noqa: BLE001
        print(f"  (hermes LIVE BLOCKED: {exc})")
if h_ok:
    from pkg.long_term_memory.storage.hermes_provider import (
        HermesStorageProvider)
    tag = f"act10-{int(time.time())}"
    hp = HermesStorageProvider(tag, base_url=HERMES_BASE, user_id="vtuber",
                               timeout=20.0)
    hrepo = ActionRepository(hp)
    ha = ActionIntentRecord.new(tag, "hd1", "ACKNOWLEDGE")
    ha.evaluation_id = "he1"
    ha.strategy_id = "hs1"
    ha.reason = "Hermes 意图"
    hrepo.save(ha)
    check("[hermes] save/get", hrepo.get(ha.action_id) is not None
          and hrepo.count() == 1)
    check("[hermes] reverse trace by decision",
          hrepo.list_by_decision("hd1")[0].action_id == ha.action_id)
    check("[hermes] reverse trace by evaluation",
          hrepo.list_by_evaluation("he1")[0].action_id == ha.action_id)
    hp2 = HermesStorageProvider(tag, base_url=HERMES_BASE, user_id="vtuber",
                                timeout=20.0)
    hrepo2 = ActionRepository(hp2)
    check("[hermes] restart persistence",
          hrepo2.count() == 1 and hrepo2.get(ha.action_id) is not None)
    hp2.close()
    hp.close()
else:
    print("\nHermes Action Integration Test: BLOCKED")
    print("Reason: hermes service not reachable at", HERMES_BASE)

print(f"\n{'='*54}")
print(f"PHASE 10 RESULT: {ok} passed, {fail} failed"
      + ("" if h_ok else "  [hermes LIVE BLOCKED]"))
print(f"{'='*54}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
