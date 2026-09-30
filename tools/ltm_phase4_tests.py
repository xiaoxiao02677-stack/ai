"""Phase 4 Experience Memory tests (§二十七).

Covers: record creation / serialization / repository CRUD / conf_uid
isolation / restart persistence / update+finalize / duplicate save /
basic capture lifecycle / graceful failure / SQLite provider / Hermes
provider (LIVE when LTM_XP_HERMES_LIVE=1 and the service is up, else
explicit BLOCKED) / forbidden-feature architecture scan.

Run local:  sshagent/Scripts/python.exe tools/ltm_phase4_tests.py
Run server: LTM_XP_SRC=src/open_llm_vtuber LTM_XP_HERMES_LIVE=1 \
            uv run python tools/ltm_phase4_tests.py
"""
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.environ.get("LTM_XP_SRC", os.path.join(ROOT, "src"))
HERMES_LIVE = os.environ.get("LTM_XP_HERMES_LIVE", "0") == "1"
HERMES_BASE = os.environ.get("LTM_HERMES_BASE_URL", "http://127.0.0.1:12396")

import shutil  # noqa: E402
WORK = tempfile.mkdtemp(prefix="ltm_p4_")
SRC_ABS = os.path.abspath(SRC)  # resolve before chdir(WORK) below
LTM_SRC = os.path.join(SRC, "open_llm_vtuber", "long_term_memory")
XP_SRC = os.path.join(SRC, "open_llm_vtuber", "experience")
PKG = os.path.join(WORK, "pkg")           # mirrors open_llm_vtuber/
LTM_PKG = os.path.join(PKG, "long_term_memory")
XP_PKG = os.path.join(PKG, "experience")
os.makedirs(os.path.join(LTM_PKG, "storage"))
os.makedirs(XP_PKG)
open(os.path.join(PKG, "__init__.py"), "w").close()
shutil.copy(os.path.join(LTM_SRC, "__init__.py"),
            os.path.join(LTM_PKG, "__init__.py"))  # real facade (get_config)
shutil.copy(os.path.join(XP_SRC, "__init__.py"),
            os.path.join(XP_PKG, "__init__.py"))

import shutil  # noqa: E402
LTM_SRC = os.path.join(SRC, "open_llm_vtuber", "long_term_memory")
XP_SRC = os.path.join(SRC, "open_llm_vtuber", "experience")
for rel in ["schemas.py", "store.py", "retriever.py", "keyword_extractor.py",
            "privacy.py", "deduplicator.py", "prompt_builder.py",
            "manager.py", "extractor.py"]:
    shutil.copy(os.path.join(LTM_SRC, rel), os.path.join(LTM_PKG, rel))
for rel in ["provider.py", "sqlite_provider.py", "repository.py",
            "provider_factory.py", "hermes_provider.py", "__init__.py"]:
    shutil.copy(os.path.join(LTM_SRC, "storage", rel),
                os.path.join(LTM_PKG, "storage", rel))
for rel in ["schemas.py", "repository.py", "engine.py", "__init__.py"]:
    shutil.copy(os.path.join(XP_SRC, rel), os.path.join(XP_PKG, rel))

SRC_ABS = os.path.abspath(SRC)
sys.path.insert(0, os.path.dirname(PKG))
os.chdir(WORK)

from pkg.long_term_memory.schemas import MemoryRecord  # noqa: F401,E402
from pkg.experience.schemas import ExperienceRecord  # noqa: E402
from pkg.experience.engine import ExperienceEngine  # noqa: E402
from pkg.experience.repository import ExperienceRepository  # noqa: E402
from pkg.long_term_memory.storage.sqlite_provider import SQLiteStorageProvider  # noqa: E402
from pkg.long_term_memory.storage.hermes_provider import (  # noqa: E402
    HermesStorageProvider, HermesUnavailableError)

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


# ---------------------------------------------------------------------------
section("1. ExperienceRecord creation + serialization")
rec = ExperienceRecord.new("confA", "hist_1", "chat")
check("new() stamps ids/timestamps",
      rec.experience_id and rec.conf_uid == "confA"
      and rec.history_uid == "hist_1" and rec.started_at > 0)
d = rec.to_dict()
rec2 = ExperienceRecord.from_dict(d)
check("to_dict/from_dict roundtrip",
      rec2.experience_id == rec.experience_id
      and rec2.conf_uid == "confA"
      and abs(rec2.started_at - rec.started_at) < 1e-9)
check("tool_calls serialization",
      (r3 := ExperienceRecord.from_dict(
          {**d, "tool_calls": [{"tool_name": "t", "status": "running"}]}
      )).tool_calls == [{"tool_name": "t", "status": "running"}])

# ---------------------------------------------------------------------------
section("2. SQLite provider: experience CRUD + restart + isolation")
p = SQLiteStorageProvider("xp_a")
repo = ExperienceRepository(p)
eng = ExperienceEngine.start("xp_a", "histA", "chat")
eng.record_user_input("用户说今天工作好累")
eng.record_tool("search", "running")
eng.record_tool("search", "success")
eng.record_ai_response("AI 安慰了用户并建议休息")
eng.record_outcome("用户表示好多了")
r = eng.finalize("turn_complete")
repo.add(r)
g = repo.get(r.experience_id)
check("add/get", g is not None and g.user_input == "用户说今天工作好累")
check("tool events captured", len(g.tool_calls) == 2
      and g.tool_calls[1]["status"] == "success")
check("outcome captured", g.outcome == "用户表示好多了")
check("finalized_at stamped", g.finalized_at > 0)

# update / re-finalize
r2 = ExperienceRecord.from_dict(r.to_dict())
r2.outcome_type = "ai_error"
repo.update(r2)
check("update persists", repo.get(r.experience_id).outcome_type == "ai_error")

# duplicate save behavior: same id saved again = overwrite, not duplicate
repo.add(ExperienceRecord.from_dict(r.to_dict()))
check("duplicate same-id save stays one row", repo.count() == 1)

# list ordering
time.sleep(0.01)
e2 = ExperienceEngine.start("xp_a", "histA2")
e2.record_user_input("第二次互动")
e2.record_ai_response("第二次回复")
repo.add(e2.finalize())
check("list_recent newest-first", repo.list_recent(10)[0].experience_id
      == e2.record.experience_id)
check("list_by_history scoping",
      len(repo.list_by_history("histA")) == 1
      and len(repo.list_by_history("histA2")) == 1)
p.close()

# --- restart persistence (brand new provider) -------------------------------
p = SQLiteStorageProvider("xp_a")
repo = ExperienceRepository(p)
check("restart: count intact", repo.count() == 2)
check("restart: get by id intact",
      repo.get(r.experience_id).user_input == "用户说今天工作好累")
check("restart: list_by_history intact", len(repo.list_by_history("histA")) == 1)

# --- conf_uid isolation -----------------------------------------------------
pb = SQLiteStorageProvider("xp_b")
rb = ExperienceRepository(pb)
eb = ExperienceEngine.start("xp_b", "hB")
eb.record_user_input("B 人格互动")
eb.record_ai_response("B 回复")
rb.add(eb.finalize())
check("isolation: B sees only B", rb.count() == 1
      and all(x.conf_uid == "xp_b" for x in rb.list_recent(100)))
check("isolation: A unchanged", repo.count() == 2
      and all(x.conf_uid == "xp_a" for x in repo.list_recent(100)))
check("isolation: cross-conf get by id impossible",
      rb.get(r.experience_id) is None)
pb.close()
p.close()

# ---------------------------------------------------------------------------
section("3. Capture lifecycle (engine state machine)")
e = ExperienceEngine.start("c", "h")
e.record_user_input("hi")
check("start->input", e.record.user_input == "hi")
e.record_ai_response("hello")
e.record_tool("x", "running")
final = e.finalize()
check("finalize stamps outcome_type/finalized_at",
      final.outcome_type == "turn_complete" and final.finalized_at > 0)
t0 = final.finalized_at
e.record_ai_response("SHOULD NOT OVERWRITE")
check("finalize is terminal (post-finalize writes refused)",
      e.record.ai_response == "hello")
e.finalize("empty_reply")
check("double finalize keeps first stamp",
      e.record.finalized_at == t0 and e.record.outcome_type == "turn_complete")
# new record (not reused) for the next episode
e2b = ExperienceEngine.start("c", "h")
check("each engine call = new experience_id",
      e2b.record.experience_id != e.record.experience_id)

# ---------------------------------------------------------------------------
section("4. Privacy: LTM policy reused, sensitive fields dropped (not stored)")
leaky = "用户说他的 API key 是 sk-abcdefgh12345678 还有密码 请记住"
repo3 = ExperienceRepository(SQLiteStorageProvider("xp_priv"))
e3 = ExperienceEngine.start("xp_priv", "h")
e3.record_user_input(leaky)
e3.record_ai_response("好的")
repo3.add(e3.finalize())
stored = repo3.get(e3.record.experience_id)
check("sensitive input dropped at capture (privacy_check semantics)",
      "sk-abcdefgh12345678" not in (stored.user_input or "")
      and "sk-" not in (stored.user_input or "")
      and stored.user_input == "")
check("drop is auditable in metadata",
      any(d["field"] == "user_input"
          for d in stored.metadata.get("privacy_dropped", [])))
e3b = ExperienceEngine.start("xp_priv", "h")
e3b.record_user_input("正常的一句话")
e3b.record_ai_response("正常的回复")
repo3.add(e3b.finalize())
check("normal content passes untouched",
      repo3.get(e3b.record.experience_id).user_input == "正常的一句话")

# ---------------------------------------------------------------------------
section("5. Failure semantics: experience persistence never breaks chat")
# Experiences ride the SAME provider config as memory. Under the hermes
# backend they persist in the provider's local sidecar — so a dead remote
# does NOT lose experience data (local reliability), and no code path
# silently writes to a different backend than configured.
dead_repo = ExperienceRepository(
    HermesStorageProvider("xp_dead", base_url="http://127.0.0.1:1",
                          timeout=1.0, verify=False))
e4 = ExperienceEngine.start("xp_dead", "h")
e4.record_user_input("hi")
e4.record_ai_response("yo")
try:
    dead_repo.add(e4.finalize())   # sidecar path: succeeds locally
    ok_dead = True
except Exception as exc:  # noqa: BLE001
    ok_dead = False
    print(f"    (sidecar write raised: {exc})")
check("hermes-sidecar experience persists even with remote down "
      "(no data loss, no cross-backend fallback)",
      ok_dead and dead_repo.get(e4.record.experience_id) is not None)
# memory operations on the same dead provider DO fail explicitly
try:
    dead_repo.provider.list_memories()
    check("memory ops on dead remote still fail explicitly", False)
except HermesUnavailableError:
    check("memory ops on dead remote still fail explicitly", True)
dead_repo.provider.close()

# ---------------------------------------------------------------------------
section("6. Hermes provider: experiences (sidecar path)")
h_ok = False
if HERMES_LIVE:
    try:
        import httpx as _hx
        _hx.get(f"{HERMES_BASE}/api/health", timeout=3).raise_for_status()
        h_ok = True
    except Exception as exc:  # noqa: BLE001
        print(f"  (hermes LIVE BLOCKED: {exc})")
if h_ok:
    tag = f"xp4-{int(time.time())}"
    hp = HermesStorageProvider(tag, base_url=HERMES_BASE, user_id="vtuber",
                               timeout=20.0)
    hrepo = ExperienceRepository(hp)
    he = ExperienceEngine.start(tag, "hh")
    he.record_user_input("Hermes 后端的一次互动")
    he.record_tool("x", "success")
    he.record_ai_response("Hermes 后端回复")
    hrepo.add(he.finalize())
    check("[hermes] add/get", hrepo.get(he.record.experience_id)
          .user_input == "Hermes 后端的一次互动")
    # restart: brand-new provider instance (sidecar reload)
    hp2 = HermesStorageProvider(tag, base_url=HERMES_BASE, user_id="vtuber",
                                timeout=20.0)
    hrepo2 = ExperienceRepository(hp2)
    check("[hermes] restart persistence",
          hrepo2.count() == 1 and hrepo2.get(he.record.experience_id)
          is not None)
    hp2.close()
    hp.close()
else:
    print("\nHermes Experience Integration Test: BLOCKED")
    print("Reason: hermes service not reachable at", HERMES_BASE)
    print("(SQLite experience tests still executed)")

# ---------------------------------------------------------------------------
section("7. Forbidden-feature architecture scan")


def _code_lines(path):
    """Yield (lineno, line) for real code only (docstrings/comments out)."""
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


FORBIDDEN = {
    "Reflection": r"reflect",
    "Lesson": r"\blesson\b",
    "Knowledge": r"knowledge",
    "Strategy": r"strateg",
    "Learning": r"\blearn",
    "Self-learning": r"self.?learn",
    "Personality evolution": r"persona.*evol|evol.*persona",
}
xp_files = [f for f in os.listdir(os.path.join(SRC_ABS, "open_llm_vtuber", "experience")) if f.endswith(".py")]
bad = {}
XP_SCAN_SRC = os.path.join(SRC_ABS, "open_llm_vtuber", "experience")
for fn in xp_files:
    for i, line in _code_lines(os.path.join(XP_SCAN_SRC, fn)):
        for name, rx in FORBIDDEN.items():
            if re.search(rx, line, re.IGNORECASE):
                bad.setdefault(fn, []).append((i, name, line.strip()[:80]))
check("experience package contains no reflection/lesson/knowledge/"
      "strategy/learning logic", not bad, str(bad))
# direct backend access (code only)
for fn in xp_files:
    hits = [ln for _, ln in _code_lines(os.path.join(SRC_ABS, "open_llm_vtuber", "experience", fn))
            if re.search(r"sqlite3|httpx\b|requests\b|HermesProvider", ln)]
    check(f"experience/{fn}: no direct sqlite3/httpx/requests/hermes",
          not hits, str(hits[:2]))

print(f"\n{'='*54}")
print(f"PHASE 4 RESULT: {ok} passed, {fail} failed"
      + ("" if h_ok else "  [hermes LIVE BLOCKED]"))
print(f"{'='*54}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
