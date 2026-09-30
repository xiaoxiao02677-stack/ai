"""Phase-2 local smoke test (no server needed).

Verifies the aggregate-shaped StorageProvider refactor:

  1. protocol conformance (SQLiteStorageProvider + FakeStorageProvider
     both isinstance StorageProvider)
  2. facade surface end-to-end (legacy MemoryStore API unchanged)
  3. direct repository usage against a raw provider
  4. legacy DB compatibility (old-schema .db file created by raw SQL
     from the Phase-1B layout)
  5. **pluggability**: a pure in-memory FakeStorageProvider is swapped
     in at the composition root; the full repository + retriever +
     facade stack runs against it unchanged (the Hermes guarantee)
  6. behavioral equivalence: identical operation scripts against the
     Phase-1B stack (backup) and the Phase-2 stack produce identical
     logical DB dumps
  7. concurrency (multi-thread writers/readers)
  8. failure path (closed provider, read-after-close)

Run with the managed Python:
    ./sshagent/Scripts/python.exe tools/ltm_phase2_smoke.py
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
# Phase-2 stack: the module dir (local mirror by default; on the server
# point LTM_SMOKE_SRC2 at src/open_llm_vtuber/long_term_memory)
SRC2 = os.environ.get("LTM_SMOKE_SRC2", os.path.join(ROOT, "src_ltm"))
# Phase-1B stack: pre-refactor layout for the equivalence run
SRC1B = os.environ.get("LTM_SMOKE_SRC1B", os.path.join(ROOT, "src_ltm_phase1b_backup"))
# schemas.py source for the 1B package (it may not sit inside SRC1B)
SCHEMAS = os.environ.get(
    "LTM_SMOKE_SCHEMAS", os.path.join(ROOT, "server_ltm_phase2", "schemas.py")
)

WORK = tempfile.mkdtemp(prefix="ltm_p2_smoke_")
PKG2 = os.path.join(WORK, "pkg2", "ltm2")
PKG1 = os.path.join(WORK, "pkg1", "ltm1")

for pkg, src in ((PKG2, SRC2), (PKG1, SRC1B)):
    os.makedirs(os.path.join(pkg, "storage"), exist_ok=True)
    open(os.path.join(pkg, "__init__.py"), "w").close()
    shutil.copy(SCHEMAS if pkg == PKG1 else os.path.join(src, "schemas.py"),
                os.path.join(pkg, "schemas.py"))
    shutil.copy(os.path.join(src, "store.py"), os.path.join(pkg, "store.py"))
    for rel in ["provider.py", "sqlite_provider.py", "__init__.py"]:
        shutil.copy(os.path.join(src, "storage", rel), os.path.join(pkg, "storage", rel))
    if pkg == PKG2:
        shutil.copy(os.path.join(src, "storage", "repository.py"),
                    os.path.join(pkg, "storage", "repository.py"))
    else:
        for rel in ["memory_repository.py", "state_repository.py",
                    "summary_repository.py", "keyword_repository.py"]:
            shutil.copy(os.path.join(src, "storage", rel), os.path.join(pkg, "storage", rel))
    # both stacks need the domain modules for the retriever; copy the rest
    for rel in ["retriever.py", "keyword_extractor.py", "privacy.py",
                "deduplicator.py", "prompt_builder.py"]:
        shutil.copy(os.path.join(SRC2, rel), os.path.join(pkg, rel))

sys.path.insert(0, os.path.dirname(PKG2))
sys.path.insert(0, os.path.dirname(PKG1))
os.chdir(WORK)  # _DATA_DIR = cwd/long_term_memory_data

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
print("== 1. protocol conformance ==")
from ltm2.storage.provider import StorageProvider  # noqa: E402
from ltm2.storage.sqlite_provider import SQLiteStorageProvider  # noqa: E402
from ltm2.storage.repository import (  # noqa: E402
    MemoryRepository, KeywordRepository, StateRepository, SummaryRepository,
)
from ltm2.schemas import MemoryRecord, KeywordRecord, UserState  # noqa: E402
from ltm2.store import MemoryStore  # noqa: E402
from ltm2.retriever import MemoryRetriever  # noqa: E402

p = SQLiteStorageProvider("proto")
check("SQLiteStorageProvider isinstance StorageProvider (aggregate)", isinstance(p, StorageProvider))
agg_methods = [
    "save_memory", "get_memory", "list_memories", "list_all_memories",
    "delete_memory", "find_active_by_content", "count_memories", "mark_used",
    "upsert_keyword", "list_keywords", "delete_keyword", "count_keywords",
    "get_state", "save_state", "get_summary", "save_summary",
    "get_turn_count", "bump_turn_count", "close",
]
check("provider exposes 19 aggregate methods",
      all(hasattr(p, m) for m in agg_methods))
check("provider no longer exposes SQL primitives",
      not any(hasattr(p, m) for m in ("execute", "query_one", "query_rows", "transaction")))
p.close()

# ---------------------------------------------------------------------------
print("== 2. facade surface end-to-end ==")
store = MemoryStore("smoke_a")
r1 = MemoryRecord.new("smoke_a", "preference", "用户喜欢吃火锅", ["火锅"], 0.8, 0.9)
r2 = MemoryRecord.new("smoke_a", "fact", "用户养了一只猫", ["猫"], 0.5, 0.9)
store.add_memory(r1)
store.add_memory(r2)
check("add/get", store.get_memory(r1.memory_id).content == "用户喜欢吃火锅")
check("list_memories", len(store.list_memories(status="active")) == 2)
check("list_memories typed", len(store.list_memories("active", "preference")) == 1)
check("list_all_memories", len(store.list_all_memories()) == 2)
before = r1.updated_at
time.sleep(0.01)
store.update_memory(r1)
check("update stamps updated_at", store.get_memory(r1.memory_id).updated_at > before)
check("find_active_by_content", store.find_active_by_content("用户养了一只猫") is not None)
check("find miss", store.find_active_by_content("nope") is None)
check("search_active", len(store.search_active(["火锅"])) == 1)
check("search_active empty terms", store.search_active([]) == [])
store.mark_used([r1.memory_id, r2.memory_id])
got = store.get_memory(r1.memory_id)
check("mark_used bumps", got.use_count == 1 and got.last_used_at >= r1.last_used_at)
check("count_memories", store.count_memories("active") == 2)
store.upsert_keyword("火锅", "food")
store.upsert_keyword("火锅", "food")
kws = store.list_keywords()
check("keyword upsert counts", any(k.keyword == "火锅" and k.hit_count == 2 for k in kws))
check("count_keywords", store.keywords.count_keywords() == 1)
check("delete_keyword", store.delete_keyword("火锅", "food") and store.keywords.count_keywords() == 0)
st = UserState(conf_uid="smoke_a", emotion="happy", current_topic="火锅")
store.save_state(st)
check("state roundtrip", store.get_state().emotion == "happy" and store.get_state().current_topic == "火锅")
check("state stamps updated_at", store.get_state().updated_at > 0)
store.save_summary("摘要", 3)
check("summary roundtrip", store.get_summary() == "摘要" and store.get_turn_count() == 3)
check("bump_turn_count", store.bump_turn_count() == 4)
check("delete_memory", store.delete_memory(r2.memory_id) and store.get_memory(r2.memory_id) is None)
s = store.stats()
check("stats composes", s["active_memories"] == 1 and s["keywords"] == 0 and s["turn_count"] == 4)
store.close()

# ---------------------------------------------------------------------------
print("== 3. direct repositories over raw provider ==")
raw = SQLiteStorageProvider("direct_repo")
mem = MemoryRepository(raw)
kr = KeywordRepository(raw)
sr = StateRepository(raw)
smr = SummaryRepository(raw)
rr = MemoryRecord.new("direct_repo", "goal", "用户准备找实习", ["实习"], 0.7, 0.8)
mem.add_memory(rr)
mem.upsert_keyword if False else kr.upsert_keyword("实习", "goal")
check("repo add/get", mem.get_memory(rr.memory_id).content == "用户准备找实习")
check("repo count", mem.count_memories() == 1)
check("repo keyword count", kr.count_keywords() == 1)
sr.save_state(UserState(conf_uid="direct_repo"))
check("repo state", sr.get_state().intent == "chat")
smr.bump_turn_count()
check("repo bump", smr.get_turn_count() == 1)
raw.close()

# ---------------------------------------------------------------------------
print("== 4. legacy DB compatibility ==")
legacy_dir = os.path.join(WORK, "long_term_memory_data")
legacy_db = os.path.join(legacy_dir, "legacy_conf.db")
conn = sqlite3.connect(legacy_db)
conn.executescript("""
CREATE TABLE memories (
    memory_id TEXT PRIMARY KEY, conf_uid TEXT NOT NULL, memory_type TEXT NOT NULL,
    content TEXT NOT NULL, keywords TEXT NOT NULL DEFAULT '[]',
    source_history_uid TEXT DEFAULT '', importance REAL DEFAULT 0.5,
    confidence REAL DEFAULT 0.8, status TEXT DEFAULT 'active',
    use_count INTEGER DEFAULT 0, created_at REAL, updated_at REAL,
    last_used_at REAL, history TEXT DEFAULT '[]');
CREATE INDEX idx_mem_conf ON memories(conf_uid, status);
CREATE TABLE keywords (
    keyword TEXT NOT NULL, category TEXT NOT NULL, conf_uid TEXT NOT NULL,
    hit_count INTEGER DEFAULT 1, first_seen_at REAL, last_seen_at REAL,
    PRIMARY KEY (keyword, category, conf_uid));
CREATE TABLE user_state (
    conf_uid TEXT PRIMARY KEY, emotion TEXT, energy REAL, stress REAL,
    current_topic TEXT, intent TEXT, updated_at REAL);
CREATE TABLE summary (
    conf_uid TEXT PRIMARY KEY, conversation_summary TEXT DEFAULT '',
    turn_count INTEGER DEFAULT 0, updated_at REAL);
INSERT INTO memories VALUES ('legacy1','legacy_conf','preference','用户喜欢喝咖啡',
    '["咖啡"]','',0.8,0.9,'active',2,1700000000,1700000000,1700000000,'[]');
""")
conn.commit()
conn.close()
lstore = MemoryStore("legacy_conf")
legacy_recs = lstore.list_memories()
check("legacy db readable", len(legacy_recs) == 1 and legacy_recs[0].content == "用户喜欢喝咖啡")
check("legacy fields mapped", legacy_recs[0].use_count == 2 and legacy_recs[0].keywords == ["咖啡"])
lret = MemoryRetriever({"max_injected_memories": 5, "min_total_score": 0.30}, lstore)
block = lret.build_prompt_block("我想喝点咖啡")
check("legacy retrieval works", block is not None and "咖啡" in block["block"])
lstore.close()

# ---------------------------------------------------------------------------
print("== 5. pluggability: FakeStorageProvider (the Hermes guarantee) ==")


class FakeStorageProvider:
    """In-memory StorageProvider: zero SQL, zero sqlite3 — proves the
    Protocol is backend-agnostic. Mirrors aggregate semantics exactly."""

    def __init__(self, conf_uid: str):
        self.conf_uid = conf_uid
        self.db_path = f"fake://{conf_uid}"
        self._lock = threading.Lock()
        self._memories: dict = {}
        self._keywords: dict = {}
        self._state = None
        self._summary = ""
        self._turns = 0
        self.closed = False

    def _ck(self):
        if self.closed:
            raise RuntimeError("provider closed")

    def save_memory(self, r):
        with self._lock:
            self._ck()
            self._memories[r.memory_id] = r

    def get_memory(self, mid):
        with self._lock:
            return self._memories.get(mid)

    def list_memories(self, status="active", memory_type=None):
        with self._lock:
            out = [r for r in self._memories.values()
                   if r.conf_uid == self.conf_uid and r.status == status
                   and (memory_type is None or r.memory_type == memory_type)]
        out.sort(key=lambda r: (-r.importance, -r.updated_at))
        return out

    def list_all_memories(self):
        with self._lock:
            out = [r for r in self._memories.values() if r.conf_uid == self.conf_uid]
        out.sort(key=lambda r: (r.status, -r.importance, -r.updated_at))
        return out

    def delete_memory(self, mid):
        with self._lock:
            return self._memories.pop(mid, None) is not None

    def find_active_by_content(self, content):
        with self._lock:
            for r in self._memories.values():
                if r.conf_uid == self.conf_uid and r.content == content and r.status == "active":
                    return r
        return None

    def count_memories(self, status="active"):
        return len(self.list_memories(status=status))

    def mark_used(self, ids):
        now = time.time()
        with self._lock:
            for mid in ids:
                r = self._memories.get(mid)
                if r:
                    r.use_count += 1
                    r.last_used_at = now

    def upsert_keyword(self, keyword, category):
        with self._lock:
            self._ck()
            key = (keyword, category)
            if key in self._keywords:
                self._keywords[key].hit_count += 1
                self._keywords[key].last_seen_at = time.time()
            else:
                self._keywords[key] = KeywordRecord.new(keyword, category, self.conf_uid)

    def list_keywords(self, limit=200):
        with self._lock:
            self._ck()
            out = list(self._keywords.values())
        out.sort(key=lambda k: (-k.hit_count, -k.last_seen_at))
        return out[:limit]

    def delete_keyword(self, keyword, category):
        with self._lock:
            self._ck()
            return self._keywords.pop((keyword, category), None) is not None

    def count_keywords(self):
        with self._lock:
            self._ck()
            return len(self._keywords)

    def get_state(self):
        with self._lock:
            self._ck()
            return self._state

    def save_state(self, state):
        with self._lock:
            self._ck()
            self._state = state

    def get_summary(self):
        with self._lock:
            self._ck()
            return self._summary

    def save_summary(self, text, turn_count):
        with self._lock:
            self._ck()
            self._summary = text
            self._turns = turn_count

    def get_turn_count(self):
        with self._lock:
            self._ck()
            return self._turns

    def bump_turn_count(self):
        with self._lock:
            self._ck()
            self._turns += 1
            return self._turns

    def close(self):
        self.closed = True


fake = FakeStorageProvider("fake_conf")
check("FakeStorageProvider isinstance StorageProvider", isinstance(fake, StorageProvider))

# swap in at the composition root — the only change a backend needs
fstore = MemoryStore.__new__(MemoryStore)
fstore.provider = fake
fstore.memories = MemoryRepository(fake)
fstore.state_repo = StateRepository(fake)
fstore.summary_repo = SummaryRepository(fake)
fstore.keywords = KeywordRepository(fake)

fr = MemoryRecord.new("fake_conf", "preference", "用户喜欢吃火锅", ["火锅"], 0.85, 0.9)
fstore.add_memory(fr)
fstore.upsert_keyword("火锅", "food")
fstore.bump_turn_count()
check("fake add/get", fstore.get_memory(fr.memory_id).content == "用户喜欢吃火锅")
check("fake keywords", fstore.keywords.count_keywords() == 1)
check("fake turns", fstore.get_turn_count() == 1)
fret = MemoryRetriever({"max_injected_memories": 5, "min_total_score": 0.30}, fstore)
fblock = fret.build_prompt_block("晚上吃火锅怎么样")
check("fake retrieval works end-to-end", fblock is not None and "火锅" in fblock["block"])
fstore.close()
try:
    fake.get_summary()
    check("fake closed raises", False)
except RuntimeError:
    check("fake closed raises", True)

# ---------------------------------------------------------------------------
print("== 6. behavioral equivalence: Phase-1B stack vs Phase-2 stack ==")

import ltm1.store as old_store_mod  # noqa: E402


def script(store, conf):
    """Same op sequence on any stack; returns a logical dump."""
    a = MemoryRecord.new(conf, "preference", "用户喜欢吃火锅", ["火锅"], 0.8, 0.9)
    b = MemoryRecord.new(conf, "fact", "用户养了一只猫叫小白", ["小白", "猫"], 0.7, 0.9)
    c = MemoryRecord.new(conf, "preference", "用户喜欢喝咖啡", ["咖啡"], 0.75, 0.9)
    for r in (a, b, c):
        store.add_memory(r)
    store.upsert_keyword("火锅", "food")
    store.upsert_keyword("火锅", "food")
    store.upsert_keyword("咖啡", "food")
    store.upsert_keyword("小白", "person")
    store.mark_used([a.memory_id])
    a.importance = 0.9
    store.update_memory(a)
    d = MemoryRecord.new(conf, "preference", "用户最近不喝咖啡了", ["咖啡"], 0.8, 0.9)
    c.status = "deprecated"
    c.history = [{"event": "deprecated_by", "reason": "状态更新", "at": 1.0}]
    store.update_memory(c)
    store.add_memory(d)
    store.save_state(UserState(conf_uid=conf, emotion="happy", current_topic="火锅"))
    store.save_summary("用户: 聊了火锅\nAI: 好的", 5)
    store.bump_turn_count()
    # logical dump: sorted rows ignoring volatile timestamps, memory_ids
    # (uuid-random) and the conf_uid scope key (test uses one per stack);
    # keyword rows likewise; user_state/summary exact
    dump = []
    for r in sorted(store.list_all_memories(), key=lambda r: (r.content, r.status)):
        dump.append((r.memory_type, r.content,
                     tuple(r.keywords), round(r.importance, 4), round(r.confidence, 4),
                     r.status, r.use_count, tuple(map(tuple, sorted(
                         (h.get("event"), h.get("reason")) for h in r.history)))))
    for k in sorted(store.list_keywords(), key=lambda k: (k.keyword, k.category)):
        dump.append(("kw", k.keyword, k.category, k.hit_count))
    st = store.get_state()
    dump.append(("state", st.emotion, st.current_topic))
    dump.append(("summary", store.get_summary(), store.get_turn_count()))
    return dump


old_store = old_store_mod.MemoryStore("eq_1b")
new_store = MemoryStore("eq_2")
d_old, d_new = script(old_store, "eq_1b"), script(new_store, "eq_2")
same = d_old == d_new
check("1B vs 2 logical DB dumps identical", same,
      "" if same else f"\n    old={d_old}\n    new={d_new}")
old_store.close()
new_store.close()

# ---------------------------------------------------------------------------
print("== 7. concurrency ==")
cc = MemoryStore("conc")
base = MemoryRecord.new("conc", "fact", "seed", [], 0.5, 0.9)
cc.add_memory(base)
errs = []


def writer(n):
    try:
        s = MemoryStore("conc")
        for i in range(20):
            r = MemoryRecord.new("conc", "fact", f"w{n}-{i}", [], 0.5, 0.9)
            s.add_memory(r)
            s.upsert_keyword(f"kw{n}_{i}", "topic")
            s.bump_turn_count()
        s.close()
    except Exception as e:  # noqa: BLE001
        errs.append(f"writer{n}: {e}")


def reader(n):
    try:
        s = MemoryStore("conc")
        for _ in range(20):
            s.list_memories()
            s.list_keywords()
            s.get_turn_count()
        s.close()
    except Exception as e:  # noqa: BLE001
        errs.append(f"reader{n}: {e}")


threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
threads += [threading.Thread(target=reader, args=(i,)) for i in range(2)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("no concurrency errors", not errs, str(errs[:3]))
check("all writes landed", cc.count_memories() == 1 + 4 * 20)
check("all turn bumps landed", cc.get_turn_count() == 4 * 20)
cc.close()

# ---------------------------------------------------------------------------
print("== 8. failure path ==")
fp = SQLiteStorageProvider("failpath")
fp.close()
try:
    fp.get_memory("x")
    check("read after close raises", False)
except Exception:
    check("read after close raises", True)

fstore2 = MemoryStore("smoke_b")
fstore2.close()
check("double close tolerated", (fstore2.close() or True))

print(f"\n{'='*50}\nRESULT: {ok} passed, {fail} failed\n{'='*50}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
