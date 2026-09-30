"""Phase 5 Reflection Engine tests (spec §八 matrix).

Covers: record CRUD / list+isolation / time-window analysis / LLM mock
(valid) / LLM output rejection (missing fields, strategy words, foreign
evidence) / conf_uid isolation / restart persistence / duplicate save /
empty-experience no-op / batch sanity / architecture scan.

Run local:  sshagent/Scripts/python.exe tools/ltm_phase5_tests.py
Run server: LTM_RFL_SRC=src LTM_RFL_HERMES_LIVE=1 \
            uv run python tools/ltm_phase5_tests.py
"""
import asyncio
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_RFL_SRC", os.path.join(ROOT, "src"))
HERMES_LIVE = os.environ.get("LTM_RFL_HERMES_LIVE", "0") == "1"
HERMES_BASE = os.environ.get("LTM_HERMES_BASE_URL", "http://127.0.0.1:12396")

import shutil  # noqa: E402
SRC_ABS = os.path.abspath(SRC)
WORK = tempfile.mkdtemp(prefix="ltm_p5_")
PKG = os.path.join(WORK, "pkg")
LTM_PKG = os.path.join(PKG, "long_term_memory")
XP_PKG = os.path.join(PKG, "experience")
RFL_PKG = os.path.join(PKG, "reflection")
os.makedirs(os.path.join(LTM_PKG, "storage"))
os.makedirs(XP_PKG)
os.makedirs(RFL_PKG)
open(os.path.join(PKG, "__init__.py"), "w").close()
for src_pkg, dst_pkg in (("long_term_memory", LTM_PKG), ("experience", XP_PKG),
                         ("reflection", RFL_PKG)):
    shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", src_pkg, "__init__.py"),
                os.path.join(dst_pkg, "__init__.py"))
for rel in ["schemas.py", "store.py", "retriever.py", "keyword_extractor.py",
            "privacy.py", "deduplicator.py", "prompt_builder.py",
            "manager.py", "extractor.py"]:
    shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", "long_term_memory", rel),
                os.path.join(LTM_PKG, rel))
for rel in ["provider.py", "sqlite_provider.py", "repository.py",
            "provider_factory.py", "hermes_provider.py", "__init__.py"]:
    shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", "long_term_memory", "storage", rel),
                os.path.join(LTM_PKG, "storage", rel))
for rel in ["schemas.py", "repository.py", "engine.py"]:
    shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", "experience", rel),
                os.path.join(XP_PKG, rel))
for rel in ["schemas.py", "repository.py", "engine.py", "analyzer.py"]:
    shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", "reflection", rel),
                os.path.join(RFL_PKG, rel))

sys.path.insert(0, os.path.dirname(PKG))
os.chdir(WORK)

from pkg.long_term_memory.store import MemoryStore  # noqa: E402
from pkg.long_term_memory.storage.sqlite_provider import SQLiteStorageProvider  # noqa: E402
from pkg.experience.repository import ExperienceRepository  # noqa: E402
from pkg.experience.engine import ExperienceEngine  # noqa: E402
from pkg.reflection.schemas import ReflectionRecord  # noqa: E402
from pkg.reflection.repository import ReflectionRepository  # noqa: E402
from pkg.reflection.engine import ReflectionEngine  # noqa: E402
from pkg.reflection.analyzer import LLMAnalyzer  # noqa: E402

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
    """Extractor-convention mock: chat_completion -> async str stream."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def chat_completion(self, messages, system_prompt):
        self.calls += 1

        async def gen():
            yield self.payload

        return gen()


def seed_experiences(conf, repo, items, base_ts=None):
    ids = []
    for i, (u, a, outcome) in enumerate(items):
        e = ExperienceEngine.start(conf, f"h{i}")
        e.record_user_input(u)
        e.record_ai_response(a)
        e.record_outcome("ok")
        rec = e.finalize(outcome)
        if base_ts is not None:  # force window placement for tests
            rec.finalized_at = base_ts + i
        repo.add(rec)
        ids.append(rec.experience_id)
    return ids


# ---------------------------------------------------------------------------
section("1. ReflectionRecord CRUD + serialization")
store = MemoryStore("rfl_a")
rrepo = ReflectionRepository(store.provider)
r = ReflectionRecord.new("rfl_a", "interaction_pattern", ["e1", "e2"])
r.observation = "过去 2 次互动中用户话题集中在工作。"
r.evidence = ["e1", "e2"]
r.confidence = 0.8
r.time_window_start, r.time_window_end = 1.0, 2.0
rrepo.save(r)
g = rrepo.get(r.reflection_id)
check("save/get roundtrip", g is not None and g.observation == r.observation)
check("fields preserved",
      g is not None and g.conf_uid == "rfl_a"
      and g.source_experience_ids == ["e1", "e2"]
      and abs(g.confidence - 0.8) < 1e-9
      and g.reflection_type == "interaction_pattern"
      and g.time_window_start == 1.0)
d = r.to_dict()
r2b = ReflectionRecord.from_dict(d)
check("to_dict/from_dict roundtrip",
      r2b.reflection_id == r.reflection_id
      and r2b.evidence == r.evidence
      and abs(r2b.time_window_end - 2.0) < 1e-9)

# duplicate save -> single record
r.observation = "过去 2 次互动中用户话题集中在工作（复核）。"
rrepo.save(r)
check("duplicate save keeps one row",
      rrepo.count() == 1 and "复核" in rrepo.get(r.reflection_id).observation)

check("get miss -> None", rrepo.get("nope") is None)
check("delete", rrepo.delete(r.reflection_id) and rrepo.count() == 0)
check("delete miss -> False", rrepo.delete("nope") is False)

# ---------------------------------------------------------------------------
section("2. conf_uid isolation + list ordering")
for conf, tag in (("rfl_a", "A"), ("rfl_b", "B")):
    repo_b = ReflectionRepository(MemoryStore(conf).provider)
    rr = ReflectionRecord.new(conf, "frequency_analysis", [f"{tag}1"])
    rr.observation = f"{tag} 组观察：共 1 条。"
    rr.evidence = [f"{tag}1"]
    repo_b.save(rr)
    if conf == "rfl_b":
        b_id = rr.reflection_id
repo_a = ReflectionRepository(MemoryStore("rfl_a").provider)
check("isolation: A only sees A",
      all(x.conf_uid == "rfl_a" for x in repo_a.list_by_conf_uid())
      and repo_a.get(b_id) is None)
repo_b = ReflectionRepository(MemoryStore("rfl_b").provider)
check("isolation: B only sees B",
      all(x.conf_uid == "rfl_b" for x in repo_b.list_by_conf_uid())
      and len(repo_b.list_by_conf_uid()) == 1)
time.sleep(0.01)
late = ReflectionRecord.new("rfl_a", "tool_usage", ["x"])
late.observation = "较晚的观察。"
late.evidence = ["x"]
repo_a.save(late)
check("list_recent newest-first",
      repo_a.list_recent(10)[0].reflection_id == late.reflection_id)
check("list filter by type",
      len(repo_a.list_recent(10, reflection_type="frequency_analysis")) == 1
      and len(repo_a.list_recent(10, reflection_type="tool_usage")) == 1
      and len(repo_a.list_recent(10, reflection_type="interaction_pattern")) == 0)

# ---------------------------------------------------------------------------
section("3. Time-window analysis (rule engine)")
wstore = MemoryStore("rfl_w")
wx = ExperienceRepository(wstore.provider)
old_id = seed_experiences("rfl_w", wx,
                          [("很久以前的一次互动", "旧回复", "turn_complete")],
                          base_ts=1000.0)[0]
new_ids = seed_experiences(
    "rfl_w", wx,
    [("我今天很累", "辛苦了，休息一下吧", "turn_complete"),
     ("又累了", "抱抱", "turn_complete"),
     ("Python 学不动了", "慢慢来", "ai_error")])
weng = ReflectionEngine(ReflectionRepository(wstore.provider), wx, config={})
recs = weng.analyze_experiences("rfl_w", start=time.time() - 3600)
check("window excludes ancient experience",
      all(old_id not in rec.source_experience_ids for rec in recs))
freq = [x for x in recs if x.reflection_type == "frequency_analysis"][0]
check("window analysis counts only in-window items",
      "3 条互动经历" in freq.observation and "1 条 AI 出错" in freq.observation)
check("four rule kinds produced", len(recs) == 4)
check("all observations fact-only",
      all(not re.search(r"应该|策略|建议|下次", x.observation) for x in recs))
check("evidence traceable",
      all(set(x.evidence) <= set(new_ids) for x in recs))

# ---------------------------------------------------------------------------
section("4. LLM analysis (mock, valid output)")
llm = FakeLLM('{"reflection_type": "interaction_pattern", '
              '"observation": "3 次互动中有 2 次用户表达疲惫后继续对话。", '
              '"evidence": ["' + new_ids[0] + '", "' + new_ids[1] + '"], '
              '"confidence": 0.8}')
leng = ReflectionEngine(ReflectionRepository(MemoryStore("rfl_w").provider),
                        ExperienceRepository(MemoryStore("rfl_w").provider),
                        config={"reflection": {"llm_analysis": True,
                                               "batch_size": 20,
                                               "llm_timeout": 5.0}},
                        llm=llm)
llm_recs = asyncio.run(leng.analyze_experiences_llm("rfl_w"))
check("llm reflection produced + persisted",
      len(llm_recs) == 1 and llm_recs[0].metadata.get("analyzer") == "llm")
check("llm evidence kept valid ids only",
      llm_recs and sorted(llm_recs[0].evidence) == sorted(new_ids[:2]))
check("llm observation stored verbatim",
      llm_recs and "表达疲惫后继续对话" in llm_recs[0].observation)

# ---------------------------------------------------------------------------
section("5. LLM output rejection (invalid / strategy / foreign evidence)")


def llm_try(payload):
    f = FakeLLM(payload)
    eng = ReflectionEngine(
        ReflectionRepository(MemoryStore("rfl_w2").provider),
        ExperienceRepository(MemoryStore("rfl_w2").provider),
        config={"reflection": {"llm_analysis": True, "llm_timeout": 5.0}},
        llm=f)
    seed_experiences("rfl_w2",
                     ExperienceRepository(MemoryStore("rfl_w2").provider),
                     [("累", "抱抱", "turn_complete")])
    out = asyncio.run(eng.analyze_experiences_llm("rfl_w2"))
    return out


check("strategy wording rejected",
      llm_try('{"observation": "以后应该主动安慰用户", "evidence": ["e1"], '
              '"confidence": 0.9}') == [])
check("invalid JSON rejected",
      llm_try("this is not json") == [])
check("foreign evidence ids rejected",
      llm_try('{"observation": "观察。", "evidence": ["made_up_id"], '
              '"confidence": 0.9}') == [])
check("empty observation rejected",
      llm_try('{"observation": "", "evidence": ["e1"], "confidence": 0.9}') == [])
out_ok = llm_try('{"observation": "1 次互动完成。", "evidence": [], '
                 '"confidence": 0.9}')
check("no-evidence output rejected (untraceable)", out_ok == [])

# ---------------------------------------------------------------------------
section("6. Restart persistence (fresh provider instances)")
s2 = MemoryStore("rfl_w")
rr2 = ReflectionRepository(s2.provider)
check("restart: rule records survive", rr2.count() == 5)  # 4 rule + 1 llm
check("restart: get by id works",
      rr2.get(recs[0].reflection_id) is not None)
check("restart: llm record survives",
      any(x.metadata.get("analyzer") == "llm" for x in rr2.list_recent(50)))

# ---------------------------------------------------------------------------
section("7. Empty experiences -> graceful no-op")
empty_eng = ReflectionEngine(
    ReflectionRepository(MemoryStore("rfl_empty").provider),
    ExperienceRepository(MemoryStore("rfl_empty").provider), config={})
check("no experiences: no records, no error",
      empty_eng.analyze_experiences("rfl_empty") == []
      and empty_eng.analyze_last_n("rfl_empty", 5) == [])
check("no llm attached: llm pass no-op",
      asyncio.run(empty_eng.analyze_experiences_llm("rfl_empty")) == [])

# ---------------------------------------------------------------------------
section("8. analyze_last_n")
nstore = MemoryStore("rfl_n")
nx = ExperienceRepository(nstore.provider)
seed_experiences("rfl_n", nx,
                 [("a", "a'", "turn_complete"), ("b", "b'", "turn_complete"),
                  ("c", "c'", "turn_complete")])
neng = ReflectionEngine(ReflectionRepository(nstore.provider), nx, config={})
nrecs = neng.analyze_last_n("rfl_n", 2)
freq_n = [x for x in nrecs if x.reflection_type == "frequency_analysis"][0]
check("analyze_last_n scopes to n items",
      "2 条互动经历" in freq_n.observation
      and len(freq_n.source_experience_ids) == 2)

# ---------------------------------------------------------------------------
section("9. Batch sanity (100 experiences)")
bstore = MemoryStore("rfl_batch")
bx = ExperienceRepository(bstore.provider)
t0 = time.time()
seed_experiences("rfl_batch", bx,
                 [(f"消息{i}", f"回复{i}", "turn_complete") for i in range(100)])
beng = ReflectionEngine(ReflectionRepository(bstore.provider), bx, config={})
brecs = beng.analyze_experiences("rfl_batch")
dt = time.time() - t0
check("100-experience analysis completes", len(brecs) == 4 and dt < 10.0)
print(f"    (batch analysis took {dt:.2f}s)")

# ---------------------------------------------------------------------------
section("10. Architecture scan (reflection package)")


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


RFL_SRC_DIR = os.path.join(SRC_ABS, "open_llm_vtuber", "reflection")
for fn in sorted(os.listdir(RFL_SRC_DIR)):
    if not fn.endswith(".py"):
        continue
    hits = [ln.strip()[:70] for _, ln in
            _code_lines(os.path.join(RFL_SRC_DIR, fn))
            if re.search(r"sqlite3|httpx\b|requests\b|HermesStorageProvider", ln)]
    check(f"reflection/{fn}: no direct backend access", not hits, str(hits[:2]))
# conversation layer untouched by reflection: engine/analyzer never imported there
conv_path = os.path.join(SRC_ABS, "open_llm_vtuber", "conversations",
                         "single_conversation.py")
if os.path.exists(conv_path):  # local mirrors may omit the app tree
    with open(conv_path, encoding="utf-8") as fh:
        conv_src = fh.read()
    check("single_conversation.py has no reflection import",
          "reflection" not in conv_src)
else:
    print("    (conversation file not in mirror; check runs on the server)")

# ---------------------------------------------------------------------------
section("11. Hermes provider: reflections (sidecar path)")
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
    tag = f"rfl5-{int(time.time())}"
    hp = HermesStorageProvider(tag, base_url=HERMES_BASE, user_id="vtuber",
                               timeout=20.0)
    hrepo = ReflectionRepository(hp)
    hr = ReflectionRecord.new(tag, "frequency_analysis", ["he1"])
    hr.observation = "Hermes 后端的反思观察：1 条互动。"
    hr.evidence = ["he1"]
    hrepo.save(hr)
    check("[hermes] save/get", hrepo.get(hr.reflection_id) is not None
          and hrepo.count() == 1)
    hp2 = HermesStorageProvider(tag, base_url=HERMES_BASE, user_id="vtuber",
                                timeout=20.0)
    hrepo2 = ReflectionRepository(hp2)
    check("[hermes] restart persistence",
          hrepo2.count() == 1 and hrepo2.get(hr.reflection_id) is not None)
    hp2.close()
    hp.close()
else:
    print("\nHermes Reflection Integration Test: BLOCKED")
    print("Reason: hermes service not reachable at", HERMES_BASE)

print(f"\n{'='*54}")
print(f"PHASE 5 RESULT: {ok} passed, {fail} failed"
      + ("" if h_ok else "  [hermes LIVE BLOCKED]"))
print(f"{'='*54}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
