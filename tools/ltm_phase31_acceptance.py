"""Phase 3.1 acceptance suite: provider verification + stability patches.

Scope (per the Phase 3.1 spec): test + locate + minimal-fix only.
Covers:

  A. SQLite regression (§四): full-field CRUD / keyword lifecycle /
     state / summary / turn-count across provider RESTARTS
  B. Hermes LIVE contract (§五-§九): real HTTP CRUD with UUID mapping,
     ID persistence across provider instances (Instance A -> B, the
     highest-priority test), metadata round-trip, sidecar persistence
     for every aggregate, duplicate-write stability, updated_at sanity
  C. Failure handling (§十,§十二): dead endpoint -> explicit
     HermesUnavailableError, verify-on-start fail-fast, no silent
     sqlite fallback anywhere
  D. Factory (§十一): sqlite / hermes / unknown
  E. Architecture isolation (§十三,§十四,§十八): repositories, manager,
     retriever never reference hermes/httpx/requests//api/agents/SQL

SQLite parts always run. Hermes LIVE parts run when the ai-companion
service is up (LTM_ACC_HERMES_LIVE=1, default auto-detect via /api/health).

Run local:  sshagent/Scripts/python.exe tools/ltm_phase31_acceptance.py
Run server: LTM_ACC_SRC=src/open_llm_vtuber/long_term_memory \
            LTM_ACC_HERMES_LIVE=1 uv run python tools/ltm_phase31_acceptance.py
"""
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC2 = os.environ.get("LTM_ACC_SRC", os.path.join(ROOT, "src_ltm"))
HERMES_LIVE = os.environ.get("LTM_ACC_HERMES_LIVE", "0") == "1"
HERMES_BASE = os.environ.get("LTM_HERMES_BASE_URL", "http://127.0.0.1:12396")

WORK = tempfile.mkdtemp(prefix="ltm_p31_acc_")
PKG = os.path.join(WORK, "ltm31")
os.makedirs(os.path.join(PKG, "storage"))
open(os.path.join(PKG, "__init__.py"), "w").close()

import shutil  # noqa: E402
for rel in ["schemas.py", "store.py", "retriever.py", "keyword_extractor.py",
            "privacy.py", "deduplicator.py", "prompt_builder.py"]:
    shutil.copy(os.path.join(SRC2, rel), os.path.join(PKG, rel))
for rel in ["provider.py", "sqlite_provider.py", "repository.py",
            "provider_factory.py", "hermes_provider.py", "__init__.py"]:
    shutil.copy(os.path.join(SRC2, "storage", rel),
                os.path.join(PKG, "storage", rel))

SRC2_ABS = os.path.abspath(SRC2)
sys.path.insert(0, os.path.dirname(PKG))
os.chdir(WORK)  # sidecar + sqlite land under WORK/long_term_memory_data

from ltm31.schemas import MemoryRecord, UserState  # noqa: E402
from ltm31.storage.sqlite_provider import SQLiteStorageProvider  # noqa: E402
from ltm31.storage.hermes_provider import (  # noqa: E402
    HermesStorageProvider, HermesUnavailableError)
from ltm31.storage.provider_factory import create_storage_provider  # noqa: E402

ok = 0
fail = 0
errors = []
sections = {}


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  OK   {name}")
    else:
        fail += 1
        errors.append(f"{name} {detail}")
        print(f"  FAIL {name} {detail}")


def section(title):
    print(f"\n== {title} ==")
    return title


# ===========================================================================
print("Phase 3.1 Acceptance Suite")
print(f"module source: {SRC2_ABS}")
print(f"hermes live:   {HERMES_LIVE} ({HERMES_BASE})")

# ---------------------------------------------------------------------------
# A. SQLite regression (§四)
# ---------------------------------------------------------------------------
sec = section("A. SQLite regression")

conf = "acc31_sqlite"
p = SQLiteStorageProvider(conf)
r = MemoryRecord.new(conf, "preference", "用户喜欢吃火锅", ["火锅"], 0.8, 0.9)
p.save_memory(r)
g = p.get_memory(r.memory_id)
check("[sqlite] create->get", g is not None and g.content == "用户喜欢吃火锅")
for field, expect in [("memory_id", r.memory_id), ("conf_uid", conf),
                      ("memory_type", "preference"), ("importance", 0.8),
                      ("confidence", 0.9), ("status", "active")]:
    check(f"[sqlite] field {field}",
          g is not None and getattr(g, field) == expect)
check("[sqlite] created_at stamped", g.created_at > 0)

time.sleep(0.01)
r2d = MemoryRecord.from_dict(r.to_dict())
r2d.content = "用户非常喜欢吃火锅"
r2d.importance = 0.95
p.save_memory(r2d)
g2 = p.get_memory(r.memory_id)
check("[sqlite] update persists", g2.content == "用户非常喜欢吃火锅"
      and abs(g2.importance - 0.95) < 1e-9)
check("[sqlite] update stamps updated_at", g2.updated_at >= g.created_at)
check("[sqlite] still exactly one row", p.count_memories("active") == 1)

p.delete_memory(r.memory_id)
check("[sqlite] delete->get None", p.get_memory(r.memory_id) is None)
check("[sqlite] count after delete", p.count_memories("active") == 0)

# keywords don't touch memories
rk = MemoryRecord.new(conf, "fact", "锚点记忆", [], 0.5, 0.9)
p.save_memory(rk)
p.upsert_keyword("火锅", "food")
p.upsert_keyword("火锅", "food")
p.upsert_keyword("小白", "person")
check("[sqlite] keyword upsert hit_count",
      any(k.keyword == "火锅" and k.hit_count == 2 for k in p.list_keywords()))
check("[sqlite] keywords don't disturb memories",
      p.count_memories("active") == 1 and p.get_memory(rk.memory_id) is not None)
p.delete_keyword("小白", "person")
check("[sqlite] keyword delete", p.count_keywords() == 1)

# state / summary / turn
st = UserState(conf_uid=conf, emotion="happy", current_topic="火锅", energy=0.8)
p.save_state(st)
p.save_summary("用户: 早前聊了火锅\nAI: 好的", 7)
p.bump_turn_count()
p.mark_used([rk.memory_id])
p.close()

# --- RESTART: brand-new provider instance, same cwd ------------------------
p = SQLiteStorageProvider(conf)
check("[sqlite] state survives restart",
      (s := p.get_state()) is not None and s.emotion == "happy"
      and s.current_topic == "火锅" and abs(s.energy - 0.8) < 1e-9)
check("[sqlite] summary survives restart",
      p.get_summary() == "用户: 早前聊了火锅\nAI: 好的")
check("[sqlite] turn count survives restart", p.get_turn_count() == 8)
check("[sqlite] memory survives restart",
      (m := p.get_memory(rk.memory_id)) is not None and m.use_count == 1)
check("[sqlite] keywords survive restart", p.count_keywords() == 1)
p.close()

# duplicate-write stability (§十七)
p = SQLiteStorageProvider(conf)
for _ in range(3):
    p.save_memory(rk)  # same memory_id repeated update
same_content = MemoryRecord.new(conf, "fact", "锚点记忆", [], 0.5, 0.9)
p.save_memory(same_content)  # same content, NEW id -> business dedup's job
check("[sqlite] repeated same-id update keeps 1 row (per id)",
      p.get_memory(rk.memory_id) is not None
      and p.count_memories("active") == 2)
p.close()

# ---------------------------------------------------------------------------
# C1. Factory (§十一) + config fail-fast (§十二) — runs before LIVE parts
# ---------------------------------------------------------------------------
sec = section("C. Factory + fail-fast")

check("[factory] sqlite selection",
      type(create_storage_provider("f1")).__name__ == "SQLiteStorageProvider")
try:
    create_storage_provider("f2", {"storage": {"provider": "nope"}})
    check("[factory] unknown provider raises", False)
except ValueError as e:
    check("[factory] unknown provider raises", "nope" in str(e))

try:
    create_storage_provider(
        "f3",
        {"storage": {"provider": "hermes",
                     "hermes": {"base_url": "http://127.0.0.1:1",
                                "timeout": 1.0}}})
    check("[factory] hermes w/ dead endpoint fails at construction", False)
except HermesUnavailableError:
    check("[factory] hermes w/ dead endpoint fails at construction", True)

# runtime ops against a dead endpoint still raise explicitly (verify=False)
dead = HermesStorageProvider(
    "f4", base_url="http://127.0.0.1:1", timeout=1.0, verify=False)
try:
    dead.get_memory("x")
    check("[hermes-dead] runtime get raises", False)
except HermesUnavailableError:
    check("[hermes-dead] runtime get raises", True)
try:
    dead.save_memory(MemoryRecord.new("f4", "fact", "x", [], 0.5, 0.9))
    check("[hermes-dead] runtime save raises", False)
except HermesUnavailableError:
    check("[hermes-dead] runtime save raises", True)
dead.close()

# ---------------------------------------------------------------------------
# B. Hermes LIVE (§五-§九) — only when the service is up
# ---------------------------------------------------------------------------
hermes_ok = False
if HERMES_LIVE:
    try:
        import httpx as _httpx
        _httpx.get(f"{HERMES_BASE}/api/health", timeout=3).raise_for_status()
        hermes_ok = True
    except Exception as e:  # noqa: BLE001
        print(f"\n(hermes LIVE BLOCKED: {e})")

if hermes_ok:
    sec = section("B. Hermes LIVE contract")

    tag = f"acc31-{int(time.time())}"

    # --- Instance A: create everything ------------------------------------
    pa = HermesStorageProvider(tag, base_url=HERMES_BASE, user_id="vtuber",
                               timeout=20.0)
    check("[hermes] isinstance available & verified",
          hasattr(pa, "save_memory") and hasattr(pa, "close"))

    ra = MemoryRecord.new(tag, "preference", "用户喜欢吃火锅", ["火锅"], 0.8, 0.9)
    rb = MemoryRecord.new(tag, "identity", "用户名叫小雪", ["小雪"], 0.95, 0.9)
    pa.save_memory(ra)
    pa.save_memory(rb)
    ga = pa.get_memory(ra.memory_id)
    check("[hermes] create->get", ga is not None
          and ga.content == "用户喜欢吃火锅")

    # full-field metadata round-trip (§八)
    check("[hermes] memory_id stable", ga.memory_id == ra.memory_id)
    check("[hermes] type roundtrip", ga.memory_type == "preference")
    check("[hermes] keywords roundtrip", ga.keywords == ["火锅"])
    check("[hermes] importance roundtrip", abs(ga.importance - 0.8) < 1e-9)
    check("[hermes] confidence roundtrip", abs(ga.confidence - 0.9) < 1e-9)
    check("[hermes] created_at sane (UTC parse, not +8h off)",
          0 < ga.created_at <= time.time() + 60)

    # UUID mapping recorded (§六)
    hid = pa._sidecar.hermes_id_of(ra.memory_id)
    check("[hermes] UUID mapping recorded", hid is not None
          and re.match(r"^[0-9a-f-]{36}$", hid) is not None)

    # update: fields + updated_at + status
    time.sleep(0.05)
    ra_u = MemoryRecord.from_dict(ra.to_dict())
    ra_u.content = "用户非常喜欢吃火锅"
    ra_u.importance = 0.95
    pa.save_memory(ra_u)
    gu = pa.get_memory(ra.memory_id)
    check("[hermes] update fields persist", gu.content == "用户非常喜欢吃火锅"
          and abs(gu.importance - 0.95) < 1e-9)
    check("[hermes] updated_at advances", gu.updated_at >= ga.created_at)
    check("[hermes] still one record for id", pa.count_memories("active") == 2)

    # deprecate (status path)
    rb_d = MemoryRecord.from_dict(rb.to_dict())
    rb_d.status = "deprecated"
    rb_d.history = [{"event": "deprecated_by", "reason": "状态更新", "at": 1.0}]
    pa.save_memory(rb_d)
    gb = pa.get_memory(rb.memory_id)
    check("[hermes] deprecate persists", gb.status == "deprecated")
    check("[hermes] history metadata persists",
          len(gb.history) == 1 and gb.history[0]["event"] == "deprecated_by")
    check("[hermes] count after deprecate",
          pa.count_memories("active") == 1
          and pa.count_memories("deprecated") == 1)

    # sidecar aggregates + use stats on Instance A
    pa.upsert_keyword("火锅", "food")
    pa.upsert_keyword("火锅", "food")
    pa.upsert_keyword("小雪", "person")
    pa.save_state(UserState(conf_uid=tag, emotion="happy",
                            current_topic="火锅"))
    pa.save_summary("用户: 聊了火锅", 5)
    pa.bump_turn_count()
    pa.mark_used([ra.memory_id, ra.memory_id])

    pa.close()

    # --- Instance B: the highest-priority test (§七) -----------------------
    sec = section("B2. ID persistence across provider instances (restart)")

    pb = HermesStorageProvider(tag, base_url=HERMES_BASE, user_id="vtuber",
                               timeout=20.0)
    gb2 = pb.get_memory(ra.memory_id)
    check("[restart] get by original memory_id succeeds",
          gb2 is not None and gb2.content == "用户非常喜欢吃火锅",
          "ID LOST" if gb2 is None else "")
    check("[restart] id mapping intact (no new UUID, no dup)",
          pb._sidecar.hermes_id_of(ra.memory_id) == hid)
    check("[restart] active+deprecated counts intact",
          pb.count_memories("active") == 1
          and pb.count_memories("deprecated") == 1)

    # sidecar aggregates after restart (§九)
    check("[restart] keywords persist",
          any(k.keyword == "火锅" and k.hit_count == 2
              for k in pb.list_keywords()))
    check("[restart] state persists",
          (s := pb.get_state()) is not None and s.emotion == "happy")
    check("[restart] summary persists", pb.get_summary() == "用户: 聊了火锅")
    check("[restart] turn count persists", pb.get_turn_count() == 6)
    gm = pb.get_memory(ra.memory_id)
    check("[restart] use_stats persist (use_count=2)",
          gm is not None and gm.use_count == 2)

    # duplicate-write stability (§十七)
    for _ in range(3):
        pb.save_memory(ra_u)  # same id repeated
    check("[restart] repeated same-id update stays 1 active record",
          pb.count_memories("active") == 1)
    dup_content = MemoryRecord.new(tag, "preference",
                                   "用户非常喜欢吃火锅", ["火锅"], 0.95, 0.9)
    pb.save_memory(dup_content)
    check("[hermes] same-content new-id is a separate record (dedup is "
          "business layer's job)",
          pb.count_memories("active") == 2)

    # retriever over LIVE hermes (§十五)
    from ltm31.store import MemoryStore  # noqa: E402
    from ltm31.retriever import MemoryRetriever  # noqa: E402
    from ltm31.storage import (MemoryRepository, KeywordRepository,
                               StateRepository, SummaryRepository)
    hs = MemoryStore.__new__(MemoryStore)
    hs.provider = pb
    hs.memories = MemoryRepository(pb)
    hs.keywords = KeywordRepository(pb)
    hs.state_repo = StateRepository(pb)
    hs.summary_repo = SummaryRepository(pb)
    ret = MemoryRetriever(
        {"max_injected_memories": 5, "min_total_score": 0.30}, hs)
    block = ret.build_prompt_block("晚上吃火锅怎么样")
    check("[hermes] retriever ranking works (project-side scoring)",
          block is not None and "火锅" in block["block"])

    # delete path
    check("[hermes] delete removes", pb.delete_memory(dup_content.memory_id))
    check("[hermes] get after delete -> None",
          pb.get_memory(dup_content.memory_id) is None)
    pb.close()
else:
    print("\nHermes Integration Test: BLOCKED")
    print("Reason: hermes service not reachable at", HERMES_BASE)
    print("(SQLite regression / factory / static checks still executed)")

# ---------------------------------------------------------------------------
# E. Static architecture isolation (§十三,§十四,§十八)
# ---------------------------------------------------------------------------
sec = section("E. Static isolation")

def code_hits(path, pattern, ignore_case=True):
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    hits = []
    in_doc = False
    rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
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
        if rx.search(line):
            hits.append((i, s[:90]))
    return hits


REPO_FILES = ["storage/repository.py"]
MGR_FILES = ["manager.py", "store.py", "retriever.py"]

for rel in REPO_FILES:
    h = code_hits(os.path.join(SRC2_ABS, rel),
                  r"hermes|httpx|requests|/api/agents")
    check(f"[isolation] {rel} clean", not h, str(h[:2]))

for rel in MGR_FILES:
    # SQL statement keywords match UPPERCASE only (case-SENSITIVE regex):
    # 'update failed' in log messages / update_memory API names are domain
    # words, not SQL
    h = code_hits(
        os.path.join(SRC2_ABS, rel),
        r"hermes|httpx|requests|/api/agents|\bsqlite\b|"
        r"\bSELECT\b|\bINSERT\b|\bUPDATE\b|\bDELETE FROM\b|CREATE TABLE",
        ignore_case=False)
    check(f"[isolation] {rel} clean", not h, str(h[:2]))

# hermes boundary: only hermes_provider touches hermes/httpx (§十八)
others = [f for f in os.listdir(os.path.join(SRC2_ABS, "storage"))
          if f.endswith(".py") and f not in ("hermes_provider.py",)]
leaks = []
for f in others:
    h = code_hits(os.path.join(SRC2_ABS, "storage", f),
                  r"httpx|/api/agents|HermesStorageProvider")
    if f not in ("provider_factory.py", "__init__.py"):
        leaks += [(f, x) for x in h]
    elif f == "provider_factory.py":
        # allowed: the lazy import line, the class-name reference in the
        # return (the factory branch IS the composition point), and config
        # strings — nothing else may touch hermes
        leaks += [(f, x) for x in h
                  if "from .hermes_provider import" not in x[1]
                  and "HermesStorageProvider(" not in x[1]]
check("[isolation] only hermes_provider implements the adapter", not leaks,
      str(leaks[:3]))

# ---------------------------------------------------------------------------
print(f"\n{'='*54}")
print(f"PHASE 3.1 RESULT: {ok} passed, {fail} failed"
      + ("" if hermes_ok else "  [hermes LIVE BLOCKED]"))
print(f"{'='*54}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
