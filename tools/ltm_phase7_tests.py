"""Phase 7 Strategy engine tests.

Covers: CRUD / list_by_lesson traceability / conf_uid isolation /
restart persistence / duplicate save / rule analyzer threshold (below
min_lessons -> no strategy) / hard-commanding rejections (incl. 策略
itself) / no-source rejection / evidence-subset check / mock LLM valid
+ rejections / empty-input no-op / conversation no-touch scan /
architecture scan / Hermes sidecar (LIVE).

Run local:  sshagent/Scripts/python.exe tools/ltm_phase7_tests.py
Run server: LTM_STR_SRC=src LTM_STR_HERMES_LIVE=1 \
            uv run python tools/ltm_phase7_tests.py
"""
import asyncio
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_STR_SRC", os.path.join(ROOT, "src"))
HERMES_LIVE = os.environ.get("LTM_STR_HERMES_LIVE", "0") == "1"
HERMES_BASE = os.environ.get("LTM_HERMES_BASE_URL", "http://127.0.0.1:12396")

import shutil  # noqa: E402
SRC_ABS = os.path.abspath(SRC)
WORK = tempfile.mkdtemp(prefix="ltm_p7_")
PKG = os.path.join(WORK, "pkg")
LTM_PKG = os.path.join(PKG, "long_term_memory")
os.makedirs(os.path.join(LTM_PKG, "storage"))
DOMAINS = {}
for name in ("experience", "reflection", "lesson", "strategy"):
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
                                 "analyzer.py"))):
    for rel in files:
        shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", dom, rel),
                    os.path.join(DOMAINS[dom], rel))

sys.path.insert(0, os.path.dirname(PKG))
os.chdir(WORK)

from pkg.long_term_memory.store import MemoryStore  # noqa: E402
from pkg.lesson.schemas import LessonRecord  # noqa: E402
from pkg.lesson.repository import LessonRepository  # noqa: E402
from pkg.strategy.schemas import StrategyRecord  # noqa: E402
from pkg.strategy.repository import StrategyRepository  # noqa: E402
from pkg.strategy.engine import StrategyEngine  # noqa: E402

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
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def chat_completion(self, messages, system_prompt):
        self.calls += 1

        async def gen():
            yield self.payload

        return gen()


def make_lesson(conf, source_reflection_ids, text, confidence=0.9):
    l = LessonRecord.new(conf, source_reflection_ids)
    l.lesson = text
    l.confidence = confidence
    l.metadata = {"analyzer": "test"}
    return l


# ---------------------------------------------------------------------------
section("1. StrategyRecord CRUD + validation")
store = MemoryStore("str_a")
srepo = StrategyRepository(store.provider)
s = StrategyRecord.new("str_a", ["l1", "l2"])
s.condition = "当用户表达疲惫时"
s.recommendation = "建议先共情，通常效果更好"
s.evidence = ["l1", "l2"]
s.confidence = 0.85
srepo.save(s)
g = srepo.get(s.strategy_id)
check("save/get roundtrip", g is not None and g.condition == s.condition
      and g.recommendation == s.recommendation)
check("fields preserved", g is not None and g.source_lesson_ids == ["l1", "l2"]
      and g.evidence == ["l1", "l2"] and abs(g.confidence - 0.85) < 1e-9)
check("to_dict/from_dict roundtrip",
      StrategyRecord.from_dict(s.to_dict()).strategy_id == s.strategy_id)

# advisory wording allowed
s2 = StrategyRecord.new("str_a", ["l1"])
s2.condition = "当用户提到工作压力"
s2.recommendation = "通常做法是先倾听再回应"
s2.evidence = ["l1"]
srepo.save(s2)
check("advisory wording allowed", srepo.get(s2.strategy_id) is not None)

# hard commanding markers rejected
for text, marker in (("用户疲惫时必须先安慰", "必须"),
                     ("必须 always comfort first", "always"),
                     ("执行既定策略安抚用户", "策略")):
    bad = StrategyRecord.new("str_a", ["l1"])
    bad.condition = "当用户疲惫时"
    bad.recommendation = text
    try:
        srepo.save(bad)
        check(f"hard-commanding '{marker}' rejected", False)
    except ValueError:
        check(f"hard-commanding '{marker}' rejected", True)

# no source rejection
bad2 = StrategyRecord.new("str_a")
bad2.condition = "当用户疲惫时"
bad2.recommendation = "建议先共情"
try:
    srepo.save(bad2)
    check("no-source strategy rejected", False)
except ValueError:
    check("no-source strategy rejected", True)

# evidence subset check
bad3 = StrategyRecord.new("str_a", ["l1"])
bad3.condition = "当用户疲惫时"
bad3.recommendation = "建议先共情"
bad3.evidence = ["ghost_id"]
try:
    srepo.save(bad3)
    check("evidence outside sources rejected", False)
except ValueError:
    check("evidence outside sources rejected", True)

# missing condition / recommendation
bad4 = StrategyRecord.new("str_a", ["l1"])
bad4.recommendation = "建议先共情"
try:
    srepo.save(bad4)
    check("missing condition rejected", False)
except ValueError:
    check("missing condition rejected", True)

# duplicate save -> one row, overwrite
s.recommendation = "建议先共情，通常效果更好（复核）"
srepo.save(s)
check("duplicate save stays one row",
      srepo.count() == 2 and "复核" in srepo.get(s.strategy_id).recommendation)
check("get miss -> None", srepo.get("nope") is None)
check("delete", srepo.delete(s2.strategy_id) and srepo.count() == 1)
check("delete miss -> False", srepo.delete("nope") is False)

# ---------------------------------------------------------------------------
section("2. conf_uid isolation + list_by_lesson")
sB = StrategyRecord.new("str_b", ["lb1"])
sB.condition = "B 情境"
sB.recommendation = "B 建议"
StrategyRepository(MemoryStore("str_b").provider).save(sB)
ra = StrategyRepository(MemoryStore("str_a").provider)
rb = StrategyRepository(MemoryStore("str_b").provider)
check("isolation: A only sees A",
      all(x.conf_uid == "str_a" for x in ra.list_strategies(100))
      and ra.get(sB.strategy_id) is None)
check("isolation: B only sees B",
      all(x.conf_uid == "str_b" for x in rb.list_strategies(100)))
by_l = ra.list_by_lesson("l1")
check("list_by_lesson scoping",
      len(by_l) >= 1 and all("l1" in x.source_lesson_ids for x in by_l))
check("list_by_lesson miss -> empty", ra.list_by_lesson("ghost") == [])

# ---------------------------------------------------------------------------
section("3. Restart persistence")
s2store = MemoryStore("str_a")
sr2 = StrategyRepository(s2store.provider)
check("restart: count intact", sr2.count() == 1)
check("restart: get by id works",
      sr2.get(s.strategy_id) is not None
      and sr2.get(s.strategy_id).strategy_id == s.strategy_id)

# ---------------------------------------------------------------------------
section("4. Rule analyzer: min_lessons threshold")
lstore = MemoryStore("str_r")
lrepo = LessonRepository(lstore.provider)
# two lessons sharing one reflection = one cluster
lsn_a = make_lesson("str_r", ["rid1"], "疲惫时共情更有效", 0.9)
lsn_b = make_lesson("str_r", ["rid1"], "疲惫场景先倾听", 0.8)
# lone lesson on its own reflection = below threshold
lsn_c = make_lesson("str_r", ["rid2"], "孤立的教训", 0.9)
for lsn in (lsn_a, lsn_b, lsn_c):
    lrepo.save(lsn)
reng = StrategyEngine(StrategyRepository(lstore.provider), lrepo,
                      config={"strategy": {"min_lessons": 2}})
strategies = reng.analyze_reflections("str_r")
check("cluster of 2 earns a strategy", len(strategies) == 1
      and set(strategies[0].source_lesson_ids) == {lsn_a.lesson_id, lsn_b.lesson_id})
check("lone lesson produces nothing",
      all(lsn_c.lesson_id not in x.source_lesson_ids for x in strategies))
check("condition references the shared observation",
      all("当互动数据" in x.condition for x in strategies))
check("recommendation carries lesson consensus",
      all("共情" in x.recommendation for x in strategies))

# ---------------------------------------------------------------------------
section("5. LLM analyzer: mock valid + rejections")
lstore2 = MemoryStore("str_l")
lrepo2 = LessonRepository(lstore2.provider)
ll = make_lesson("str_l", ["ridL"], "疲惫时先共情再回应", 0.9)
lrepo2.save(ll)
cfg = {"strategy": {"llm_analysis": True, "llm_timeout": 5.0}}


def llm_pass(payload):
    f = FakeLLM(payload)
    eng = StrategyEngine(StrategyRepository(MemoryStore("str_l").provider),
                         LessonRepository(MemoryStore("str_l").provider),
                         config=cfg, llm=f)
    return asyncio.run(eng.analyze_reflections_llm("str_l"))


out = llm_pass('{"condition": "当用户表达疲惫", '
               '"recommendation": "建议先共情再回应", '
               '"source_lesson_ids": ["' + ll.lesson_id + '"], '
               '"confidence": 0.8}')
check("valid llm strategy produced + persisted",
      len(out) == 1 and out[0].metadata.get("analyzer") == "llm")
check("valid llm sources kept",
      out and out[0].source_lesson_ids == [ll.lesson_id])
check("valid llm condition/recommendation stored",
      out and out[0].condition == "当用户表达疲惫"
      and "共情" in out[0].recommendation)

check("hard-commanding llm output rejected",
      llm_pass('{"condition": "疲惫", "recommendation": "必须安抚", '
               '"source_lesson_ids": ["' + ll.lesson_id + '"], '
               '"confidence": 0.9}') == [])
check("invalid json rejected", llm_pass("not json") == [])
check("foreign lesson ids rejected",
      llm_pass('{"condition": "x", "recommendation": "y", '
               '"source_lesson_ids": ["ghost"], "confidence": 0.9}') == [])
check("missing condition rejected",
      llm_pass('{"condition": "", "recommendation": "y", '
               '"source_lesson_ids": ["' + ll.lesson_id + '"], '
               '"confidence": 0.9}') == [])
check("empty sources rejected",
      llm_pass('{"condition": "x", "recommendation": "y", '
               '"source_lesson_ids": [], "confidence": 0.9}') == [])

# ---------------------------------------------------------------------------
section("6. Empty input + no LLM attached")
empty_eng = StrategyEngine(StrategyRepository(MemoryStore("str_e").provider),
                           LessonRepository(MemoryStore("str_e").provider),
                           config={})
check("no lessons: no strategies, no error",
      empty_eng.analyze_reflections("str_e") == [])
check("no llm attached: llm pass no-op",
      asyncio.run(empty_eng.analyze_reflections_llm("str_e")) == [])

# ---------------------------------------------------------------------------
section("7. Architecture scan (strategy package + conversation untouched)")


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


STR_SRC_DIR = os.path.join(SRC_ABS, "open_llm_vtuber", "strategy")
for fn in sorted(os.listdir(STR_SRC_DIR)):
    if not fn.endswith(".py"):
        continue
    hits = [ln.strip()[:70] for _, ln in _code_lines(os.path.join(STR_SRC_DIR, fn))
            if re.search(r"sqlite3|httpx\b|requests\b|HermesStorageProvider", ln)]
    check(f"strategy/{fn}: no direct backend access", not hits, str(hits[:2]))
conv_path = os.path.join(SRC_ABS, "open_llm_vtuber", "conversations",
                         "single_conversation.py")
if os.path.exists(conv_path):
    with open(conv_path, encoding="utf-8") as fh:
        conv_src = fh.read()
    check("single_conversation.py has no strategy import",
          "strategy" not in conv_src)
else:
    print("    (conversation file not in mirror; check runs on the server)")

# ---------------------------------------------------------------------------
section("8. Hermes provider: strategies (sidecar path)")
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
    tag = f"str7-{int(time.time())}"
    hp = HermesStorageProvider(tag, base_url=HERMES_BASE, user_id="vtuber",
                               timeout=20.0)
    hrepo = StrategyRepository(hp)
    hs = StrategyRecord.new(tag, ["hl1"])
    hs.condition = "Hermes 情境"
    hs.recommendation = "Hermes 建议"
    hrepo.save(hs)
    check("[hermes] save/get", hrepo.get(hs.strategy_id) is not None
          and hrepo.count() == 1)
    check("[hermes] list_by_lesson",
          hrepo.list_by_lesson("hl1")[0].strategy_id == hs.strategy_id)
    hp2 = HermesStorageProvider(tag, base_url=HERMES_BASE, user_id="vtuber",
                                timeout=20.0)
    hrepo2 = StrategyRepository(hp2)
    check("[hermes] restart persistence",
          hrepo2.count() == 1 and hrepo2.get(hs.strategy_id) is not None)
    hp2.close()
    hp.close()
else:
    print("\nHermes Strategy Integration Test: BLOCKED")
    print("Reason: hermes service not reachable at", HERMES_BASE)

print(f"\n{'='*54}")
print(f"PHASE 7 RESULT: {ok} passed, {fail} failed"
      + ("" if h_ok else "  [hermes LIVE BLOCKED]"))
print(f"{'='*54}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
