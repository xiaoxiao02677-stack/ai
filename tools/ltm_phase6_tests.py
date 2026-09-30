"""Phase 6 Lesson engine tests (spec §六 matrix).

Covers: CRUD / conf_uid isolation / restart persistence / duplicate
save / analyzer schema (mock LLM valid + forbidden-word + foreign-id
rejections) / rule analyzer (min_support gating) / empty-input no-op /
conversation no-touch scan / architecture scan / Hermes sidecar (LIVE).

Run local:  sshagent/Scripts/python.exe tools/ltm_phase6_tests.py
Run server: LTM_LSN_SRC=src LTM_LSN_HERMES_LIVE=1 \
            uv run python tools/ltm_phase6_tests.py
"""
import asyncio
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_LSN_SRC", os.path.join(ROOT, "src"))
HERMES_LIVE = os.environ.get("LTM_LSN_HERMES_LIVE", "0") == "1"
HERMES_BASE = os.environ.get("LTM_HERMES_BASE_URL", "http://127.0.0.1:12396")

import shutil  # noqa: E402
SRC_ABS = os.path.abspath(SRC)
WORK = tempfile.mkdtemp(prefix="ltm_p6_")
PKG = os.path.join(WORK, "pkg")
LTM_PKG = os.path.join(PKG, "long_term_memory")
XP_PKG = os.path.join(PKG, "experience")
RFL_PKG = os.path.join(PKG, "reflection")
LSN_PKG = os.path.join(PKG, "lesson")
os.makedirs(os.path.join(LTM_PKG, "storage"))
for p in (XP_PKG, RFL_PKG, LSN_PKG):
    os.makedirs(p)
open(os.path.join(PKG, "__init__.py"), "w").close()
for src_pkg, dst_pkg in (("long_term_memory", LTM_PKG), ("experience", XP_PKG),
                         ("reflection", RFL_PKG), ("lesson", LSN_PKG)):
    shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", src_pkg, "__init__.py"),
                os.path.join(dst_pkg, "__init__.py"))
for rel in ["schemas.py", "store.py", "retriever.py", "keyword_extractor.py",
            "privacy.py", "deduplicator.py", "prompt_builder.py",
            "manager.py", "extractor.py"]:
    shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", "long_term_memory", rel),
                os.path.join(LTM_PKG, rel))
for rel in ["provider.py", "sqlite_provider.py", "repository.py",
            "provider_factory.py", "hermes_provider.py", "__init__.py"]:
    shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", "long_term_memory",
                             "storage", rel), os.path.join(LTM_PKG, "storage", rel))
for rel in ["schemas.py", "repository.py", "engine.py"]:
    shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", "experience", rel),
                os.path.join(XP_PKG, rel))
for rel in ["schemas.py", "repository.py", "engine.py", "analyzer.py"]:
    shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", "reflection", rel),
                os.path.join(RFL_PKG, rel))
for rel in ["schemas.py", "repository.py", "engine.py", "analyzer.py"]:
    shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", "lesson", rel),
                os.path.join(LSN_PKG, rel))

sys.path.insert(0, os.path.dirname(PKG))
os.chdir(WORK)

from pkg.long_term_memory.store import MemoryStore  # noqa: E402
from pkg.experience.repository import ExperienceRepository  # noqa: E402
from pkg.experience.engine import ExperienceEngine  # noqa: E402
from pkg.reflection.repository import ReflectionRepository  # noqa: E402
from pkg.reflection.schemas import ReflectionRecord  # noqa: E402
from pkg.lesson.schemas import LessonRecord  # noqa: E402
from pkg.lesson.repository import LessonRepository  # noqa: E402
from pkg.lesson.engine import LessonEngine  # noqa: E402
from pkg.lesson.analyzer import LLMAnalyzer  # noqa: E402

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


def make_reflection(conf, rid_sources, observation, confidence=0.9):
    r = ReflectionRecord.new(conf, "interaction_pattern", rid_sources)
    r.observation = observation
    r.evidence = list(rid_sources)
    r.confidence = confidence
    r.metadata = {"analyzer": "test"}
    return r


# ---------------------------------------------------------------------------
section("1. LessonRecord CRUD + serialization + validation")
store = MemoryStore("lsn_a")
lrepo = LessonRepository(store.provider)
l = LessonRecord.new("lsn_a", ["r1", "r2"])
l.lesson = "用户疲惫时先共情再给建议，效果更好"
l.confidence = 0.8
lrepo.save(l)
g = lrepo.get(l.lesson_id)
check("save/get roundtrip", g is not None and g.lesson == l.lesson)
check("fields preserved", g is not None and g.conf_uid == "lsn_a"
      and g.source_reflection_ids == ["r1", "r2"]
      and abs(g.confidence - 0.8) < 1e-9)
check("to_dict/from_dict roundtrip",
      LessonRecord.from_dict(l.to_dict()).lesson_id == l.lesson_id)

# advisory wording ALLOWED (informs, not commands)
l2 = LessonRecord.new("lsn_a", ["r1"])
l2.lesson = "疲惫场景建议先共情，通常更适合继续对话"
lrepo.save(l2)
check("advisory wording allowed",
      lrepo.get(l2.lesson_id) is not None)

# hard policy markers rejected
bad = LessonRecord.new("lsn_a", ["r1"])
bad.lesson = "用户疲惫时必须先安慰"
try:
    lrepo.save(bad)
    check("hard-policy marker rejected", False)
except ValueError:
    check("hard-policy marker rejected", True)
bad2 = LessonRecord.new("lsn_a", ["r1"])
bad2.lesson = "you should always comfort first"
try:
    lrepo.save(bad2)
    check("english policy marker rejected", False)
except ValueError:
    check("english policy marker rejected", True)
bad3 = LessonRecord.new("lsn_a")   # no sources on purpose
bad3.lesson = "没有来源的教训"
try:
    lrepo.save(bad3)
    check("missing source_reflection_ids rejected", False)
except ValueError:
    check("missing source_reflection_ids rejected", True)

# duplicate save -> one row, content overwrites
l.lesson = "用户疲惫时先共情再给建议，效果更好（复核）"
lrepo.save(l)
check("duplicate save stays one row",
      lrepo.count() == 2 and "复核" in lrepo.get(l.lesson_id).lesson)
check("get miss -> None", lrepo.get("nope") is None)
check("delete", lrepo.delete(l2.lesson_id) and lrepo.count() == 1)
check("delete miss -> False", lrepo.delete("nope") is False)

# ---------------------------------------------------------------------------
section("2. conf_uid isolation + list_by_reflection")
lB = LessonRecord.new("lsn_b", ["rb1"])
lB.lesson = "B 人格的教训"
LessonRepository(MemoryStore("lsn_b").provider).save(lB)
ra = LessonRepository(MemoryStore("lsn_a").provider)
rb = LessonRepository(MemoryStore("lsn_b").provider)
check("isolation: A only sees A",
      all(x.conf_uid == "lsn_a" for x in ra.list_lessons(100))
      and ra.get(lB.lesson_id) is None)
check("isolation: B only sees B",
      all(x.conf_uid == "lsn_b" for x in rb.list_lessons(100)))
by_ref = ra.list_by_reflection("r1")
check("list_by_reflection scoping",
      len(by_ref) >= 1 and all("r1" in x.source_reflection_ids for x in by_ref))
check("list_by_reflection miss -> empty",
      ra.list_by_reflection("ghost") == [])

# ---------------------------------------------------------------------------
section("3. Restart persistence")
s2 = MemoryStore("lsn_a")
lr2 = LessonRepository(s2.provider)
check("restart: count intact", lr2.count() == 1)
check("restart: get by id works",
      lr2.get(l.lesson_id) is not None
      and lr2.get(l.lesson_id).lesson_id == l.lesson_id)

# ---------------------------------------------------------------------------
section("4. Rule analyzer: min_support gating")
rstore = MemoryStore("lsn_r")
rrepo = ReflectionRepository(rstore.provider)
strong = make_reflection("lsn_r", [f"e{i}" for i in range(7)],
                         "用户 7 次表达疲惫", 0.9)
weak = make_reflection("lsn_r", ["e0"], "只见过一次的观察", 0.9)
for r in (strong, weak):
    rrepo.save(r)
reng = LessonEngine(LessonRepository(rstore.provider), rrepo,
                    config={"lesson": {"min_support": 5}})
lessons = reng.analyze_reflections("lsn_r")
check("strong reflection earns a lesson", len(lessons) == 1
      and lessons[0].source_reflection_ids == [strong.reflection_id])
check("weak reflection produces nothing",
      all(x.source_reflection_ids != [weak.reflection_id] for x in lessons))
check("lesson text is observation-based",
      all("7 次" in x.lesson for x in lessons))
check("confidence bounded", all(0 < x.confidence <= 0.95 for x in lessons))

# ---------------------------------------------------------------------------
section("5. LLM analyzer: mock valid + rejections")
rstore2 = MemoryStore("lsn_l")
rrepo2 = ReflectionRepository(rstore2.provider)
rr = make_reflection("lsn_l", [f"e{i}" for i in range(6)],
                     "用户疲惫时倾向于继续倾诉", 0.9)
rrepo2.save(rr)
cfg = {"lesson": {"llm_analysis": True, "llm_timeout": 5.0,
                  "batch_size": 20}}


def llm_pass(payload):
    f = FakeLLM(payload)
    leng = LessonEngine(LessonRepository(MemoryStore("lsn_l").provider),
                        ReflectionRepository(MemoryStore("lsn_l").provider),
                        config=cfg, llm=f)
    out = asyncio.run(leng.analyze_reflections_llm("lsn_l"))
    return out, f


out, _ = llm_pass('{"lesson": "疲惫时先共情通常更适合继续对话", '
                  '"source_reflection_ids": ["' + rr.reflection_id + '"], '
                  '"confidence": 0.85}')
check("valid llm lesson produced + persisted",
      len(out) == 1 and out[0].metadata.get("analyzer") == "llm")
check("valid llm sources kept",
      out and out[0].source_reflection_ids == [rr.reflection_id])
check("valid llm lesson stored verbatim",
      out and "先共情" in out[0].lesson)

check("hard-policy llm output rejected",
      llm_pass('{"lesson": "必须主动安慰", '
               '"source_reflection_ids": ["' + rr.reflection_id + '"], '
               '"confidence": 0.9}')[0] == [])
check("invalid json rejected", llm_pass("not json")[0] == [])
check("foreign reflection ids rejected",
      llm_pass('{"lesson": "共情优先", '
               '"source_reflection_ids": ["ghost_id"], '
               '"confidence": 0.9}')[0] == [])
check("missing lesson text rejected",
      llm_pass('{"lesson": "", '
               '"source_reflection_ids": ["' + rr.reflection_id + '"], '
               '"confidence": 0.9}')[0] == [])
check("empty source ids rejected",
      llm_pass('{"lesson": "共情优先", '
               '"source_reflection_ids": [], "confidence": 0.9}')[0] == [])

# ---------------------------------------------------------------------------
section("6. Empty input + no LLM attached")
empty_eng = LessonEngine(LessonRepository(MemoryStore("lsn_e").provider),
                         ReflectionRepository(MemoryStore("lsn_e").provider),
                         config={})
check("no reflections: no lessons, no error",
      empty_eng.analyze_reflections("lsn_e") == [])
check("no llm attached: llm pass no-op",
      asyncio.run(empty_eng.analyze_reflections_llm("lsn_e")) == [])

# ---------------------------------------------------------------------------
section("7. Architecture scan (lesson package + conversation untouched)")


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


LSN_SRC_DIR = os.path.join(SRC_ABS, "open_llm_vtuber", "lesson")
for fn in sorted(os.listdir(LSN_SRC_DIR)):
    if not fn.endswith(".py"):
        continue
    hits = [ln.strip()[:70] for _, ln in _code_lines(os.path.join(LSN_SRC_DIR, fn))
            if re.search(r"sqlite3|httpx\b|requests\b|HermesStorageProvider", ln)]
    check(f"lesson/{fn}: no direct backend access", not hits, str(hits[:2]))
conv_path = os.path.join(SRC_ABS, "open_llm_vtuber", "conversations",
                         "single_conversation.py")
if os.path.exists(conv_path):
    with open(conv_path, encoding="utf-8") as fh:
        conv_src = fh.read()
    check("single_conversation.py has no lesson import", "lesson" not in conv_src)
else:
    print("    (conversation file not in mirror; check runs on the server)")

# ---------------------------------------------------------------------------
section("8. Hermes provider: lessons (sidecar path)")
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
    tag = f"lsn6-{int(time.time())}"
    hp = HermesStorageProvider(tag, base_url=HERMES_BASE, user_id="vtuber",
                               timeout=20.0)
    hrepo = LessonRepository(hp)
    hl = LessonRecord.new(tag, ["hr1"])
    hl.lesson = "Hermes 后端的教训：共情优先"
    hrepo.save(hl)
    check("[hermes] save/get", hrepo.get(hl.lesson_id) is not None
          and hrepo.count() == 1)
    check("[hermes] list_by_reflection",
          hrepo.list_by_reflection("hr1")[0].lesson_id == hl.lesson_id)
    hp2 = HermesStorageProvider(tag, base_url=HERMES_BASE, user_id="vtuber",
                                timeout=20.0)
    hrepo2 = LessonRepository(hp2)
    check("[hermes] restart persistence",
          hrepo2.count() == 1 and hrepo2.get(hl.lesson_id) is not None)
    hp2.close()
    hp.close()
else:
    print("\nHermes Lesson Integration Test: BLOCKED")
    print("Reason: hermes service not reachable at", HERMES_BASE)

print(f"\n{'='*54}")
print(f"PHASE 6 RESULT: {ok} passed, {fail} failed"
      + ("" if h_ok else "  [hermes LIVE BLOCKED]"))
print(f"{'='*54}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
