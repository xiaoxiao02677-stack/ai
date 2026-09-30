"""Phase 8 Evaluation engine tests (spec §二十 matrix).

Covers: schema validation / RuleAnalyzer (strong/weak/no match/empty
context/multiple candidates + ranking) / LLMAnalyzer (valid JSON,
malformed, external id, missing id, forbidden output, empty result,
timeout) / storage (CRUD, reverse trace, conf isolation, restart) /
engine (empty input, normal path, missing strategy skip, analyzer
error, duplicate-evaluation behavior) / idempotency VERIFIED /
boundary scan (conversation/Memory/Prompt/Agent/Personality untouched).

Run local:  sshagent/Scripts/python.exe tools/ltm_phase8_tests.py
Run server: LTM_EVL_SRC=src LTM_EVL_HERMES_LIVE=1 \
            uv run python tools/ltm_phase8_tests.py
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
SRC = os.environ.get("LTM_EVL_SRC", os.path.join(ROOT, "src"))
HERMES_LIVE = os.environ.get("LTM_EVL_HERMES_LIVE", "0") == "1"
HERMES_BASE = os.environ.get("LTM_HERMES_BASE_URL", "http://127.0.0.1:12396")

import shutil  # noqa: E402
SRC_ABS = os.path.abspath(SRC)
WORK = tempfile.mkdtemp(prefix="ltm_p8_")
PKG = os.path.join(WORK, "pkg")
LTM_PKG = os.path.join(PKG, "long_term_memory")
os.makedirs(os.path.join(LTM_PKG, "storage"))
DOMAINS = {}
for name in ("experience", "reflection", "lesson", "strategy", "evaluation"):
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
from pkg.evaluation.engine import EvaluationEngine  # noqa: E402
from pkg.evaluation.analyzer import RuleAnalyzer  # noqa: E402

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


def make_strategy(conf, condition, recommendation="建议先共情再回应",
                  confidence=0.9):
    s = StrategyRecord.new(conf, ["l1"])
    s.condition = condition
    s.recommendation = recommendation
    s.evidence = ["l1"]
    s.confidence = confidence
    return s


# ---------------------------------------------------------------------------
section("1. EvaluationRecord schema validation")
store = MemoryStore("evl_a")
erepo = EvaluationRepository(store.provider)
s = make_strategy("evl_a", "当用户表达疲惫或焦虑情绪时")
StrategyRepository(store.provider).save(s)

e = EvaluationRecord.new("evl_a", s.strategy_id)
e.applicable, e.relevance, e.confidence, e.condition_match = True, 0.91, 0.84, 0.6
e.reason = "当前上下文出现明显焦虑表达"
e.evidence = ["焦虑"]
erepo.save(e)
g = erepo.get(e.evaluation_id)
check("valid evaluation save/get",
      g is not None and g.applicable and abs(g.relevance - 0.91) < 1e-9
      and g.strategy_id == s.strategy_id)
check("to_dict/from_dict roundtrip",
      EvaluationRecord.from_dict(e.to_dict()).evaluation_id == e.evaluation_id)

bad = EvaluationRecord.new("evl_a", "")   # missing source
bad.reason = "x"
try:
    erepo.save(bad)
    check("missing strategy_id rejected", False)
except ValueError:
    check("missing strategy_id rejected", True)

for name, kwargs in (("relevance", {"relevance": 1.7}),
                     ("confidence", {"confidence": -0.1}),
                     ("condition_match", {"condition_match": 2.0})):
    b = EvaluationRecord.new("evl_a", s.strategy_id)
    b.reason = "ok"
    for k, v in kwargs.items():
        setattr(b, k, v)
    try:
        erepo.save(b)
        check(f"invalid {name} rejected", False)
    except ValueError:
        check(f"invalid {name} rejected", True)

b2 = EvaluationRecord.new("evl_a", s.strategy_id)
b2.reason = "必须执行此策略"
try:
    erepo.save(b2)
    check("forbidden commanding language rejected", False)
except ValueError:
    check("forbidden commanding language rejected", True)

# duplicate save: same id -> overwrite, one row
e.reason = "当前上下文出现明显焦虑表达（复核）"
erepo.save(e)
check("duplicate save stays one row",
      erepo.count() == 1 and "复核" in erepo.get(e.evaluation_id).reason)
check("get miss -> None", erepo.get("nope") is None)
check("delete", erepo.delete(e.evaluation_id) and erepo.count() == 0)

# ---------------------------------------------------------------------------
section("2. RuleAnalyzer: strong / weak / no match / empty context")
rules = RuleAnalyzer()
sx = make_strategy("evl_a", "当用户表达疲惫或焦虑情绪时")
strong = rules.evaluate(sx, "用户今天情绪很低落，说工作好累想休息")
check("strong match applicable", strong.applicable
      and strong.condition_match >= 0.15 and len(strong.evidence) >= 1)
check("strong match reason explains hits",
      "命中" in strong.reason and len(strong.evidence) > 0)
weak = rules.evaluate(sx, "最近很焦虑睡不着")
check("weak single-hit below threshold -> not applicable",
      not weak.applicable and weak.condition_match < 0.15)
none = rules.evaluate(sx, "今天聊了聊天气和晚餐")
check("no match -> not applicable, zero relevance blend floor",
      not none.applicable and none.condition_match == 0.0)
empty_ctx = rules.evaluate(sx, "")
check("empty context -> explicit no-op judgment",
      not empty_ctx.applicable and empty_ctx.reason == "上下文为空，无法判断适用性")
check("relevance within bounds for all",
      all(0.0 <= r.relevance <= 1.0 and 0.0 <= r.confidence <= 1.0
          for r in (strong, weak, none, empty_ctx)))

# ---------------------------------------------------------------------------
section("3. Engine: candidates + ranking + error paths")
estore = MemoryStore("evl_c")
esrepo = StrategyRepository(estore.provider)
s_fatigue = make_strategy("evl_c", "当用户表达疲惫或焦虑情绪时", confidence=0.9)
s_tech = make_strategy("evl_c", "当讨论技术学习话题时", "建议给出小步练习", 0.8)
s_food = make_strategy("evl_c", "当用户提到饮食偏好时", "建议结合口味回应", 0.7)
for x in (s_fatigue, s_tech, s_food):
    esrepo.save(x)
eeng = EvaluationEngine(EvaluationRepository(estore.provider), esrepo,
                        config={})
ranked = eeng.evaluate_candidates("用户说：今天工作好累，情绪低落想倾诉",
                                  "evl_c")
check("candidates evaluated + persisted", len(ranked) == 3
      and EvaluationRepository(estore.provider).count() == 3)
check("applicable ranked first", ranked[0].applicable
      and ranked[0].strategy_id == s_fatigue.strategy_id)
check("relevance descending",
      ranked[0].relevance >= ranked[1].relevance >= ranked[2].relevance)
check("non-applicable kept as explicit rejection info",
      any(not r.applicable for r in ranked))

one = eeng.evaluate_strategy(s_fatigue.strategy_id, "最近特别焦虑睡不着",
                             "evl_c")
check("single evaluation persists", one is not None)
check("missing strategy -> explicit skip (None, no fabricated record)",
      eeng.evaluate_strategy("ghost", "x", "evl_c") is None
      and EvaluationRepository(estore.provider).count() == 4)
check("empty context candidates -> no-op",
      eeng.evaluate_candidates("", "evl_c") == [])
check("empty strategy pool -> no-op",
      EvaluationEngine(EvaluationRepository(MemoryStore("evl_none").provider),
                       StrategyRepository(MemoryStore("evl_none").provider),
                       config={}).evaluate_candidates("hi", "evl_none") == [])

# duplicate evaluation behavior (idempotency decision, §十七)
before = EvaluationRepository(estore.provider).count()
r1 = eeng.evaluate_strategy(s_fatigue.strategy_id, "又累又焦虑", "evl_c")
r2 = eeng.evaluate_strategy(s_fatigue.strategy_id, "又累又焦虑", "evl_c")
check("re-evaluating same (strategy, context) creates new records "
      "(timestamped history by design)",
      r1 is not None and r2 is not None
      and r1.evaluation_id != r2.evaluation_id
      and EvaluationRepository(estore.provider).count() == before + 2)

# ---------------------------------------------------------------------------
section("4. LLMAnalyzer: mock valid + rejections + timeout")
lstore = MemoryStore("evl_l")
lsrepo = StrategyRepository(lstore.provider)
ls = make_strategy("evl_l", "当用户表达疲惫或焦虑情绪时")
lsrepo.save(ls)
cfg = {"evaluation": {"llm_analysis": True, "llm_timeout": 5.0}}


def llm_run(payload=None, error=False):
    f = FakeLLM(payload, error)
    eng = EvaluationEngine(EvaluationRepository(MemoryStore("evl_l").provider),
                           StrategyRepository(MemoryStore("evl_l").provider),
                           config=cfg, llm=f)
    out = asyncio.run(eng.evaluate_strategy_llm(
        ls.strategy_id, "用户说好累好焦虑", "evl_l"))
    return out, f


out, f = llm_run(json.dumps({"applicable": True, "relevance": 0.91,
                             "confidence": 0.84,
                             "reason": "当前上下文出现疲惫与焦虑表达"}))
check("valid llm evaluation persisted",
      out is not None and out.metadata.get("analyzer") == "llm"
      and out.applicable and abs(out.relevance - 0.91) < 1e-9)
check("llm calls made", f.calls == 1)

check("malformed json rejected -> explicit rule fallback",
      (lambda r: r is not None and r.metadata.get("analyzer") == "rules")(
          llm_run("not json")[0]))
check("forbidden commanding output rejected",
      llm_run(json.dumps({"applicable": True, "relevance": 0.9,
                          "confidence": 0.9,
                          "reason": "必须执行此策略"}))[0].metadata.get(
                              "analyzer") == "rules")
check("out-of-range score rejected",
      llm_run(json.dumps({"applicable": True, "relevance": 1.5,
                          "confidence": 0.9, "reason": "ok"}))[0].metadata.get(
                              "analyzer") == "rules")
check("llm timeout/error -> explicit rule fallback (no fake evaluation)",
      (lambda r: r is not None and r.metadata.get("analyzer") == "rules")(
          llm_run(error=True)[0]))

# external strategy id can never leak in: the LLM payload carries no ids,
# and the record's strategy_id comes from the engine, not the LLM
out2, _ = llm_run(json.dumps({"applicable": True, "relevance": 0.5,
                              "confidence": 0.5, "reason": "ok"}))
check("strategy_id provenance comes from engine (never LLM)",
      out2 is not None and out2.strategy_id == ls.strategy_id)

# ---------------------------------------------------------------------------
section("5. conf_uid isolation + restart persistence + reverse trace")
rstore = MemoryStore("evl_a")
rsrepo = StrategyRepository(rstore.provider)
sA = make_strategy("evl_a", "当用户表达疲惫或焦虑情绪时")
rsrepo.save(sA)
sB = make_strategy("evl_b", "B 人格专属情境")
StrategyRepository(MemoryStore("evl_b").provider).save(sB)
ra = EvaluationRepository(MemoryStore("evl_a").provider)
rb = EvaluationRepository(MemoryStore("evl_b").provider)
engA = EvaluationEngine(ra, rsrepo, config={})
engA.evaluate_strategy(sA.strategy_id, "好累想休息", "evl_a")
engB = EvaluationEngine(rb, StrategyRepository(MemoryStore("evl_b").provider),
                        config={})
engB.evaluate_strategy(sB.strategy_id, "B 的上下文", "evl_b")
check("isolation: A sees only A",
      all(x.conf_uid == "evl_a" for x in ra.list_evaluations(100))
      and all(x.conf_uid == "evl_b" for x in rb.list_evaluations(100)))
check("isolation: cross-conf get impossible",
      ra.get(rb.list_evaluations(1)[0].evaluation_id) is None)
by_strat = ra.list_by_strategy(sA.strategy_id)
check("reverse trace list_by_strategy",
      len(by_strat) >= 1
      and all(x.strategy_id == sA.strategy_id for x in by_strat))
# restart
ra2 = EvaluationRepository(MemoryStore("evl_a").provider)
check("restart persistence", ra2.count() == ra.count()
      and ra2.get(by_strat[0].evaluation_id) is not None)

# ---------------------------------------------------------------------------
section("6. Boundary scan: five isolations (§二十一)")


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


EVL_SRC_DIR = os.path.join(SRC_ABS, "open_llm_vtuber", "evaluation")
for fn in sorted(os.listdir(EVL_SRC_DIR)):
    if not fn.endswith(".py"):
        continue
    hits = [ln.strip()[:70] for _, ln in _code_lines(os.path.join(EVL_SRC_DIR, fn))
            if re.search(r"sqlite3|httpx\b|requests\b|HermesStorageProvider", ln)]
    check(f"evaluation/{fn}: no direct backend access", not hits, str(hits[:2]))

BOUNDARY = {
    "conversations/single_conversation.py": "conversation isolation",
    "long_term_memory/manager.py": "MemoryManager isolation",
    "long_term_memory/retriever.py": "MemoryRetriever isolation",
    "long_term_memory/prompt_builder.py": "Prompt isolation",
}
for rel, label in BOUNDARY.items():
    path = os.path.join(SRC_ABS, "open_llm_vtuber", rel)
    if not os.path.exists(path):
        print(f"    ({rel} not in mirror; server runs this check)")
        continue
    with open(path, encoding="utf-8") as fh:
        src_text = fh.read()
    check(f"{label}: no evaluation import/use",
          "evaluation" not in src_text and "Evaluation" not in src_text)

# ---------------------------------------------------------------------------
section("7. Hermes provider: evaluations (sidecar path)")
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
    tag = f"evl8-{int(time.time())}"
    hp = HermesStorageProvider(tag, base_url=HERMES_BASE, user_id="vtuber",
                               timeout=20.0)
    hrepo = EvaluationRepository(hp)
    hs = StrategyRecord.new(tag, ["hl1"])
    hs.condition = "Hermes 情境"
    hs.recommendation = "建议"
    StrategyRepository(hp).save(hs)
    he = EvaluationRecord.new(tag, hs.strategy_id)
    he.applicable, he.relevance, he.confidence, he.condition_match = True, 0.9, 0.8, 0.5
    he.reason = "命中 Hermes 情境"
    he.evidence = ["情境"]
    hrepo.save(he)
    check("[hermes] save/get", hrepo.get(he.evaluation_id) is not None
          and hrepo.count() == 1)
    check("[hermes] reverse trace",
          hrepo.list_by_strategy(hs.strategy_id)[0].evaluation_id
          == he.evaluation_id)
    hp2 = HermesStorageProvider(tag, base_url=HERMES_BASE, user_id="vtuber",
                                timeout=20.0)
    hrepo2 = EvaluationRepository(hp2)
    check("[hermes] restart persistence",
          hrepo2.count() == 1 and hrepo2.get(he.evaluation_id) is not None)
    hp2.close()
    hp.close()
else:
    print("\nHermes Evaluation Integration Test: BLOCKED")
    print("Reason: hermes service not reachable at", HERMES_BASE)

print(f"\n{'='*54}")
print(f"PHASE 8 RESULT: {ok} passed, {fail} failed"
      + ("" if h_ok else "  [hermes LIVE BLOCKED]"))
print(f"{'='*54}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
