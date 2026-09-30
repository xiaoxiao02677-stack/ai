"""StorageProvider contract tests (Phase 3 acceptance §19-§21).

One contract suite, three backends:

  1. SQLiteStorageProvider   — the real file backend
  2. HermesStorageProvider   — over an httpx.MockTransport that faithfully
                               mimics the ai-companion Hermes Memory REST
                               semantics (CRUD + lifecycle guard + paging)
  3. HermesStorageProvider   — LIVE against http://127.0.0.1:12396 when
                               LTM_HERMES_LIVE=1 is set (server run)

Every backend must PASS the same assertions (create/get/find/delete/
metadata round-trip/namespace isolation/state/summary/keywords/retriever
stack). Capability differences are explicit (marked capability, never a
silent fake pass).

Static dependency checks (§23) run at the end: only hermes_provider may
import the hermes adapter; manager/store/repository/retriever never do.

Run:  sshagent/Scripts/python.exe tools/ltm_phase3_contract.py
      LTM_HERMES_LIVE=1 ... (on the server, against the real service)
"""
import json
import os
import re
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
# module source: local mirror by default; on the server point
# LTM_CONTRACT_SRC at src/open_llm_vtuber/long_term_memory
SRC2 = os.environ.get(
    "LTM_CONTRACT_SRC", os.path.join(ROOT, "src_ltm"))

WORK = tempfile.mkdtemp(prefix="ltm_p3_contract_")
PKG = os.path.join(WORK, "ltm3")
os.makedirs(os.path.join(PKG, "storage"))
open(os.path.join(PKG, "__init__.py"), "w").close()

for rel in ["schemas.py", "store.py", "retriever.py", "keyword_extractor.py",
            "privacy.py", "deduplicator.py", "prompt_builder.py"]:
    shutil_copy = __import__("shutil").copy
    shutil_copy(os.path.join(SRC2, rel), os.path.join(PKG, rel))
for rel in ["provider.py", "sqlite_provider.py", "repository.py",
            "provider_factory.py", "hermes_provider.py", "__init__.py"]:
    __import__("shutil").copy(
        os.path.join(SRC2, "storage", rel), os.path.join(PKG, "storage", rel))

# static checks run after chdir — resolve the module dir while the
# original cwd (repo root) still applies
SRC2_ABS = os.path.abspath(SRC2)

sys.path.insert(0, os.path.dirname(PKG))
os.chdir(WORK)

from ltm3.schemas import MemoryRecord, UserState, KeywordRecord  # noqa: E402
from ltm3.store import MemoryStore  # noqa: E402
from ltm3.retriever import MemoryRetriever  # noqa: E402
from ltm3.storage import StorageProvider  # noqa: E402
from ltm3.storage.sqlite_provider import SQLiteStorageProvider  # noqa: E402
from ltm3.storage.hermes_provider import (  # noqa: E402
    HermesStorageProvider, HermesUnavailableError)

import httpx  # noqa: E402

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


# ---------------------------------------------------------------------------
# Hermes mock: faithful ai-companion REST semantics
# ---------------------------------------------------------------------------

class FakeHermesServer:
    """In-memory ai-companion /api/agents/{id}/memories with the real
    lifecycle guard, soft-delete, X-User-Id scoping and paging."""

    def __init__(self):
        self.store = {}       # hermes uuid -> record dict
        self.requests = []    # (method, path) audit log
        self._lock = threading.Lock()

    # -- httpx handler -------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        path = request.url.path
        method = request.method
        body = json.loads(request.content or b"{}")
        m = re.match(r"^/api/agents/([^/]+)/memories(?:/([^/?]+))?$", path)
        if not m:
            return httpx.Response(404, json={"detail": "not found"})
        agent_id, mem_id = m.group(1), m.group(2)
        user = request.headers.get("x-user-id", "")
        if not user:
            return httpx.Response(422, json={"detail": "X-User-Id required"})
        with self._lock:
            if method == "GET" and mem_id:
                rec = self._get(mem_id, user, agent_id)
                if rec is None:
                    return httpx.Response(404, json={"detail": "not found"})
                return httpx.Response(200, json=rec)
            if method == "GET":
                items = self._list(user, agent_id, body, request)
                return httpx.Response(200, json={"items": items, "total": len(items)})
            if method == "POST":
                rec = self._create(user, agent_id, body)
                return httpx.Response(201, json=rec)
            if method == "PATCH":
                rec = self._patch(mem_id, user, agent_id, body)
                if isinstance(rec, httpx.Response):
                    return rec
                return httpx.Response(200, json=rec)
            if method == "DELETE":
                gone = self._delete(mem_id, user, agent_id)
                if gone is None:
                    return httpx.Response(404, json={"detail": "not found"})
                return httpx.Response(204)
        return httpx.Response(405)

    def _list(self, user, agent, body, request):
        params = request.url.params
        limit = int(params.get("limit", 50))
        offset = int(params.get("offset", 0))
        recs = [r for r in self.store.values()
                if r["user_id"] == user and r["agent_id"] == agent]
        recs.sort(key=lambda r: r["updated_at"], reverse=True)
        return recs[offset:offset + limit]

    def _get(self, mem_id, user, agent):
        r = self.store.get(mem_id)
        if not r or r["user_id"] != user or r["agent_id"] != agent:
            return None
        return r

    def _create(self, user, agent, body):
        import time as _t
        import uuid as _u
        rid = str(_u.uuid4())
        now = _t.time()
        rec = {
            "id": rid, "user_id": user, "agent_id": agent,
            "memory_type": body.get("memory_type", "semantic"),
            "content": body.get("content", ""),
            "status": "active",
            "importance": body.get("importance", 0.5),
            "confidence": body.get("confidence", 0.8),
            "access_count": 0, "last_accessed_at": None,
            "source": body.get("source", "manual"), "session_id": None,
            "metadata": body.get("metadata") or {},
            "supersedes_id": None,
            "created_at": now, "updated_at": now, "expires_at": None,
        }
        self.store[rid] = rec
        return rec

    # lifecycle guard mirrors ai_companion _TRANSITIONS
    _TRANSITIONS = {
        ("active", "superseded"), ("active", "expired"), ("active", "archived"),
        ("active", "deleted"),
        ("superseded", "deleted"), ("superseded", "archived"),
        ("expired", "deleted"), ("expired", "archived"), ("expired", "active"),
        ("archived", "active"), ("archived", "deleted"),
    }

    def _patch(self, mem_id, user, agent, body):
        r = self._get(mem_id, user, agent)
        if r is None:
            return httpx.Response(404, json={"detail": "not found"})
        if body.get("content") is not None:
            r["content"] = body["content"]
        if body.get("importance") is not None:
            r["importance"] = body["importance"]
        if body.get("confidence") is not None:
            r["confidence"] = body["confidence"]
        if body.get("metadata") is not None:
            r["metadata"] = body["metadata"]
        if body.get("status") is not None and body["status"] != r["status"]:
            if (r["status"], body["status"]) not in self._TRANSITIONS:
                return httpx.Response(
                    422, json={"detail": "illegal lifecycle transition"})
            r["status"] = body["status"]
        import time as _t
        r["updated_at"] = _t.time()
        return r

    def _delete(self, mem_id, user, agent):
        r = self._get(mem_id, user, agent)
        if r is None:
            return None
        r["status"] = "deleted"  # soft delete, same as ai_companion
        return r


# ---------------------------------------------------------------------------
# The contract suite — backend-agnostic
# ---------------------------------------------------------------------------

def contract_suite(label, make_provider):
    """Run the full StorageProvider contract against one backend factory."""
    print(f"\n== contract: {label} ==")
    conf = f"ct_{label}"
    p = make_provider(conf)
    check(f"[{label}] isinstance StorageProvider", isinstance(p, StorageProvider))

    # --- create / get / metadata round-trip --------------------------------
    r1 = MemoryRecord.new(conf, "preference", "用户喜欢吃火锅", ["火锅"], 0.85, 0.9)
    r2 = MemoryRecord.new(conf, "fact", "用户养了一只猫叫小白", ["小白", "猫"], 0.7, 0.9)
    r3 = MemoryRecord.new(conf, "identity", "用户名叫小雪", ["小雪"], 0.95, 0.9)
    p.save_memory(r1)
    p.save_memory(r2)
    p.save_memory(r3)
    g1 = p.get_memory(r1.memory_id)
    check(f"[{label}] create/get roundtrip",
          g1 is not None and g1.content == "用户喜欢吃火锅")
    check(f"[{label}] metadata keywords roundtrip",
          g1 is not None and g1.keywords == ["火锅"])
    check(f"[{label}] importance roundtrip",
          g1 is not None and abs(g1.importance - 0.85) < 1e-6)
    check(f"[{label}] type roundtrip",
          g1 is not None and g1.memory_type == "preference")
    check(f"[{label}] get miss -> None", p.get_memory("nonexistent") is None)

    # --- list / filter / count ----------------------------------------------
    actives = p.list_memories(status="active")
    check(f"[{label}] list active count", len(actives) == 3)
    check(f"[{label}] list filter by type",
          len(p.list_memories(status="active", memory_type="fact")) == 1)
    check(f"[{label}] count_memories", p.count_memories("active") == 3)
    allrecs = p.list_all_memories()
    check(f"[{label}] list_all count", len(allrecs) == 3)

    # --- update (field + status) ---------------------------------------------
    r1b = MemoryRecord.from_dict(r1.to_dict())
    r1b.content = "用户非常喜欢吃火锅"
    r1b.importance = 0.95
    p.save_memory(r1b)
    g1b = p.get_memory(r1.memory_id)
    check(f"[{label}] update fields persist",
          g1b is not None and g1b.content == "用户非常喜欢吃火锅"
          and abs(g1b.importance - 0.95) < 1e-6)

    # deprecate (status change) — the conflict-resolution path
    r2d = MemoryRecord.from_dict(r2.to_dict())
    r2d.status = "deprecated"
    r2d.history = [{"event": "deprecated_by", "reason": "状态更新", "at": 1.0}]
    p.save_memory(r2d)
    g2 = p.get_memory(r2.memory_id)
    check(f"[{label}] status deprecate persists",
          g2 is not None and g2.status == "deprecated")
    check(f"[{label}] history metadata persists",
          g2 is not None and len(g2.history) == 1
          and g2.history[0]["event"] == "deprecated_by")
    check(f"[{label}] count after deprecate",
          p.count_memories("active") == 2
          and p.count_memories("deprecated") == 1)
    check(f"[{label}] list_all includes deprecated",
          len(p.list_all_memories()) == 3)

    # --- find_active_by_content (dedup support) -------------------------------
    found = p.find_active_by_content("用户非常喜欢吃火锅")
    check(f"[{label}] find_active_by_content hit",
          found is not None and found.memory_id == r1.memory_id)
    check(f"[{label}] find_active_by_content excludes deprecated",
          p.find_active_by_content("用户养了一只猫叫小白") is None)

    # --- delete ------------------------------------------------------------------
    check(f"[{label}] delete removes", p.delete_memory(r3.memory_id) is True)
    check(f"[{label}] get after delete -> None",
          p.get_memory(r3.memory_id) is None)
    check(f"[{label}] delete miss -> False",
          p.delete_memory("nonexistent") is False)
    check(f"[{label}] count after delete", p.count_memories("active") == 1)

    # --- mark_used (capability: hermes keeps stats provider-side) -----------
    p.mark_used([r1.memory_id, r1.memory_id])
    gu = p.get_memory(r1.memory_id)
    check(f"[{label}] mark_used bumps use_count",
          gu is not None and gu.use_count == 2)
    check(f"[{label}] mark_used stamps last_used_at",
          gu is not None and gu.last_used_at > 0)

    # --- keywords aggregate ----------------------------------------------------
    p.upsert_keyword("火锅", "food")
    p.upsert_keyword("火锅", "food")
    p.upsert_keyword("小白", "person")
    kws = p.list_keywords()
    check(f"[{label}] keywords upsert counts",
          any(k.keyword == "火锅" and k.hit_count == 2 for k in kws))
    check(f"[{label}] keywords count", p.count_keywords() == 2)
    check(f"[{label}] keywords delete",
          p.delete_keyword("小白", "person") is True
          and p.count_keywords() == 1)

    # --- state / summary / turn count -------------------------------------------
    st = UserState(conf_uid=conf, emotion="happy", current_topic="火锅")
    p.save_state(st)
    gs = p.get_state()
    check(f"[{label}] state roundtrip",
          gs is not None and gs.emotion == "happy" and gs.current_topic == "火锅")
    p.save_summary("用户: 聊了火锅\nAI: 好的", 5)
    check(f"[{label}] summary roundtrip",
          p.get_summary() == "用户: 聊了火锅\nAI: 好的"
          and p.get_turn_count() == 5)
    check(f"[{label}] bump_turn_count", p.bump_turn_count() == 6)

    # --- namespace isolation -----------------------------------------------------
    other = make_provider(f"ct_{label}_other")
    r_other = MemoryRecord.new(f"ct_{label}_other", "fact", "另一人格的记忆", [], 0.5, 0.9)
    other.save_memory(r_other)
    check(f"[{label}] namespace isolated (list)",
          len(p.list_all_memories()) == 2
          and all(r.conf_uid == conf for r in p.list_all_memories()))
    check(f"[{label}] namespace isolated (find)",
          p.find_active_by_content("另一人格的记忆") is None)
    other.close()

    # --- retriever stack over this provider ---------------------------------------
    store = MemoryStore.__new__(MemoryStore)
    from ltm3.storage import MemoryRepository, KeywordRepository, \
        StateRepository, SummaryRepository
    store.provider = p
    store.memories = MemoryRepository(p)
    store.keywords = KeywordRepository(p)
    store.state_repo = StateRepository(p)
    store.summary_repo = SummaryRepository(p)
    ret = MemoryRetriever(
        {"max_injected_memories": 5, "min_total_score": 0.30}, store)
    block = ret.build_prompt_block("晚上吃火锅怎么样")
    check(f"[{label}] retriever stack works",
          block is not None and "火锅" in block["block"])
    p.close()


# ---------------------------------------------------------------------------
# 1. SQLite contract
# ---------------------------------------------------------------------------
contract_suite("sqlite", lambda conf: SQLiteStorageProvider(conf))

# ---------------------------------------------------------------------------
# 2. Hermes contract over mock transport
# ---------------------------------------------------------------------------
fake_server = FakeHermesServer()


def make_hermes_mock(conf):
    transport = httpx.MockTransport(fake_server.handler)
    prov = HermesStorageProvider.__new__(HermesStorageProvider)
    # construct without touching network-facing __init__ side effects;
    # verify=False because the mock has no /api/health route
    HermesStorageProvider.__init__(
        prov, conf, base_url="http://hermes.test",
        user_id="vtuber", timeout=5.0, verify=False)
    # swap in the mock transport (rebuild the client on it)
    prov._client = httpx.Client(
        base_url="http://hermes.test", timeout=5.0,
        headers=prov._headers, transport=transport)
    return prov


contract_suite("hermes-mock", make_hermes_mock)

# ---------------------------------------------------------------------------
# 3. Hermes LIVE contract (only when LTM_HERMES_LIVE=1)
# ---------------------------------------------------------------------------
if os.environ.get("LTM_HERMES_LIVE") == "1":
    base = os.environ.get("LTM_HERMES_BASE_URL", "http://127.0.0.1:12396")
    # unique namespace per run: the live DB persists between runs, and the
    # contract suite assumes a fresh scope
    import time as _time
    live_tag = f"hermes-live-{int(_time.time())}"
    contract_suite(
        live_tag,
        lambda conf: HermesStorageProvider(
            conf, base_url=base, user_id="vtuber", timeout=15.0))
else:
    print("\n(hermes-live skipped: set LTM_HERMES_LIVE=1 to run against "
          "the real service)")

# ---------------------------------------------------------------------------
# 4. Hermes unavailability is explicit (no silent fallback) §14
# ---------------------------------------------------------------------------
print("\n== hermes unavailable -> explicit error ==")


def make_hermes_dead(conf):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(503, json={"detail": "down"}))
    prov = make_hermes_mock(conf)
    prov._client = httpx.Client(
        base_url="http://hermes.test", timeout=2.0,
        headers=prov._headers, transport=transport)
    return prov


dead = make_hermes_dead("dead_conf")
try:
    dead.list_all_memories()
    check("unavailable raises HermesUnavailableError", False)
except HermesUnavailableError:
    check("unavailable raises HermesUnavailableError", True)
dead.close()

# factory: unknown provider fails fast, no silent sqlite fallback
from ltm3.storage.provider_factory import create_storage_provider  # noqa: E402
try:
    create_storage_provider("x", {"storage": {"provider": "redis"}})
    check("unknown provider fails fast", False)
except ValueError:
    check("unknown provider fails fast", True)
check("factory default is sqlite",
      type(create_storage_provider("x")).__name__ == "SQLiteStorageProvider")
hermes_via_factory = create_storage_provider("x", {"storage": {
    "provider": "hermes",
    "hermes": {"base_url": "http://127.0.0.1:1",
               "verify_on_start": False}}})
check("factory hermes branch builds adapter",
      type(hermes_via_factory).__name__ == "HermesStorageProvider")
hermes_via_factory.close()
try:
    create_storage_provider("x", {"storage": {
        "provider": "hermes",
        "hermes": {"base_url": "http://127.0.0.1:1", "timeout": 1.0}}})
    check("factory hermes w/ dead endpoint fails fast (verify_on_start)",
          False)
except HermesUnavailableError:
    check("factory hermes w/ dead endpoint fails fast (verify_on_start)",
          True)

# ---------------------------------------------------------------------------
# 5. Static dependency checks (§23)
# ---------------------------------------------------------------------------
print("\n== static dependency checks (§23) ==")
BUSINESS = ["manager.py", "store.py", "retriever.py", "schemas.py",
            "deduplicator.py", "extractor.py", "keyword_extractor.py",
            "prompt_builder.py", "privacy.py",
            "storage/provider.py", "storage/repository.py",
            "storage/sqlite_provider.py"]


def _hermes_code_hits(path):
    """Real-code lines mentioning hermes; docstrings/comments excluded
    (same state machine as the smoke boundary scan)."""
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    hits = []
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
        if "hermes" in line.lower():
            hits.append((i, s[:80]))
    return hits


bad = {}
for rel in BUSINESS:
    path = os.path.join(SRC2_ABS, rel)
    h = _hermes_code_hits(path)
    if h:
        bad[rel] = h
check("business layer never references hermes in code", not bad, str(bad))

# module facade config block: "hermes" as a config KEY string is the
# config layer knowing the name (allowed by §23) — assert it's only that
mod_hits = _hermes_code_hits(os.path.join(SRC2_ABS, "__init__.py"))
config_string_only = all(
    '"hermes"' in ln or "'hermes'" in ln for _, ln in mod_hits)
check("module facade mentions hermes only as config key",
      config_string_only, str(mod_hits))

# factory references hermes only via the lazy import + config strings — allowed
with open(os.path.join(SRC2_ABS, "storage", "provider_factory.py"),
          encoding="utf-8") as f:
    fac_src = f.read()
check("factory imports hermes adapter lazily",
      "from .hermes_provider import HermesStorageProvider" in fac_src)

print(f"\n{'='*50}\nRESULT: {ok} passed, {fail} failed\n{'='*50}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
