"""Phase-1B local smoke test (no server needed).

Builds a throwaway package that mirrors the real module layout
(schemas.py + store.py + storage/), then exercises:
  1. protocol conformance (isinstance checks against StorageProvider)
  2. facade 21 public methods end-to-end
  3. direct repository usage against a raw SQLiteStorageProvider
  4. legacy DB compatibility (old-schema .db file created by raw SQL)
  5. concurrency (multi-thread writers/readers)
  6. failure path (transaction rollback / provider closed)
  7. stats() composition equals raw SQL counts

Run with the managed Python 3.13.
"""
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import traceback

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src_ltm")
REMOTE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                      "remote_src", "open_llm_vtuber", "long_term_memory")

WORK = tempfile.mkdtemp(prefix="ltm_smoke_")
PKG = os.path.join(WORK, "ltm_pkg")
os.makedirs(os.path.join(PKG, "storage"))
open(os.path.join(PKG, "__init__.py"), "w").close()
open(os.path.join(PKG, "storage", "__init__.py"), "w").close()

# schemas.py lives outside src_ltm/; take it from the authoritative remote copy
shutil.copy(os.path.join(REMOTE, "schemas.py"), os.path.join(PKG, "schemas.py"))
for rel in ["store.py"]:
    shutil.copy(os.path.join(SRC, rel), os.path.join(PKG, rel))
for rel in [
    "provider.py",
    "sqlite_provider.py",
    "memory_repository.py",
    "state_repository.py",
    "summary_repository.py",
    "keyword_repository.py",
    "__init__.py",
]:
    shutil.copy(os.path.join(SRC, "storage", rel), os.path.join(PKG, "storage", rel))

os.chdir(WORK)  # _DATA_DIR = cwd/long_term_memory_data
sys.path.insert(0, WORK)

from ltm_pkg.store import MemoryStore
from ltm_pkg.schemas import MemoryRecord, UserState
from ltm_pkg.storage import (
    StorageProvider,
    StorageTransaction,
    SQLiteStorageProvider,
    MemoryRepository,
    StateRepository,
    SummaryRepository,
    KeywordRepository,
)
from ltm_pkg.storage.provider import StorageProvider as P2

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


print("== 1. protocol conformance ==")
p = SQLiteStorageProvider("proto")
check("SQLiteStorageProvider isinstance StorageProvider", isinstance(p, StorageProvider))
check("provider has conf_uid/db_path", p.conf_uid == "proto" and p.db_path.endswith("proto.db"))
check("provider exposes primitives", all(hasattr(p, m) for m in
      ("execute", "query_one", "query_rows", "row_to_memory_record", "transaction", "close")))
with p.transaction() as tx:
    check("transaction handle isinstance StorageTransaction", isinstance(tx, StorageTransaction))
p.close()

print("== 2. facade end-to-end (21 methods) ==")
store = MemoryStore("smoke_a")
s0 = store.stats()
check("stats empty", s0["active_memories"] == 0 and s0["keywords"] == 0 and s0["turn_count"] == 0)
check("stats db_path", s0["db_path"] == store.db_path)

r1 = MemoryRecord.new("smoke_a", "preference", "用户喜欢吃火锅", ["火锅"], 0.8, 0.9)
r2 = MemoryRecord.new("smoke_a", "fact", "用户养了一只猫", ["猫"], 0.5, 0.9)
r3 = MemoryRecord.new("smoke_a", "preference", "用户喝咖啡", ["咖啡"], 0.7, 0.9)
store.add_memory(r1)
store.add_memory(r2)
store.add_memory(r3)
check("add/get", store.get_memory(r1.memory_id).content == "用户喜欢吃火锅")
check("list_memories", len(store.list_memories(status="active")) == 3)
check("list_memories typed", len(store.list_memories("active", "preference")) == 2)
check("list_all_memories", len(store.list_all_memories()) == 3)

before = r1.updated_at
time.sleep(0.01)
r1.content = "用户非常喜欢吃火锅"
store.update_memory(r1)
got = store.get_memory(r1.memory_id)
check("update_memory stamps", got.updated_at > before and got.content == "用户非常喜欢吃火锅")

check("find_active_by_content", store.find_active_by_content("用户养了一只猫") is not None)
check("find_active_by_content miss", store.find_active_by_content("nope") is None)
hits = store.search_active(["火锅"])
check("search_active", len(hits) == 1 and hits[0].memory_id == r1.memory_id)
check("search_active empty terms", store.search_active([]) == [])

uc0 = got.use_count
store.mark_used([r1.memory_id, r2.memory_id])
check("mark_used bumps", store.get_memory(r1.memory_id).use_count == uc0 + 1)

r3.status = "deprecated"
store.update_memory(r3)
check("count active", store.count_memories("active") == 2)
check("count deprecated", store.count_memories("deprecated") == 1)

store.upsert_keyword("火锅", "food")
store.upsert_keyword("火锅", "food")
store.upsert_keyword("  ", "food")
store.upsert_keyword("Python", "technology")
kws = {k.keyword: k.hit_count for k in store.list_keywords()}
check("upsert_keyword increments", kws.get("火锅") == 2, str(kws))
check("upsert_keyword blank ignored", "  " not in kws and len(kws) == 2)
check("delete_keyword", store.delete_keyword("Python", "technology") is True)
check("delete_keyword idempotent", store.delete_keyword("Python", "technology") is False)

st = UserState(conf_uid="smoke_a")
st.emotion = "happy"
st.current_topic = "火锅"
store.save_state(st)
loaded = store.get_state()
check("state roundtrip", loaded.emotion == "happy" and loaded.current_topic == "火锅")
check("state defaults", loaded.energy == 0.5 and loaded.intent == "chat")
check("get_state missing scope", MemoryStore("smoke_empty").get_state() is None)

store.save_summary("用户: 你好\nAI: 好呀", 5)
check("summary roundtrip", "你好" in store.get_summary())
# NB: save_summary is INSERT OR REPLACE -> it overwrites turn_count with the
# caller-supplied value. That is the ORIGINAL single-file semantics, preserved
# verbatim by Phase-1A/1B; asserted here so it can never silently drift.
check("save_summary REPLACEs turn_count (legacy semantics)", store.get_turn_count() == 5,
      f"got {store.get_turn_count()}")
check("bump_turn_count", store.bump_turn_count() == 6, f"got {store.get_turn_count()}")
check("bump_turn_count 2", store.bump_turn_count() == 7)
store.save_summary("覆盖", 9)
check("save_summary overwrite", store.get_summary() == "覆盖" and store.get_turn_count() == 9)

check("delete_memory", store.delete_memory(r2.memory_id) is True)
check("delete_memory idempotent", store.delete_memory(r2.memory_id) is False)
check("delete_memory scoped", MemoryStore("smoke_b").delete_memory(r1.memory_id) is False)

s2 = store.stats()
check("stats composed active", s2["active_memories"] == 1, str(s2))
check("stats composed deprecated", s2["deprecated_memories"] == 1)
check("stats composed keywords", s2["keywords"] == 1)
check("stats composed turn_count", s2["turn_count"] == 9)

print("== 3. raw-SQL truth check on stats() ==")
conn = sqlite3.connect(store.db_path)
raw_a = conn.execute("SELECT COUNT(*) FROM memories WHERE conf_uid=? AND status='active'", ("smoke_a",)).fetchone()[0]
raw_d = conn.execute("SELECT COUNT(*) FROM memories WHERE conf_uid=? AND status='deprecated'", ("smoke_a",)).fetchone()[0]
raw_k = conn.execute("SELECT COUNT(*) FROM keywords WHERE conf_uid=?", ("smoke_a",)).fetchone()[0]
raw_t = conn.execute("SELECT turn_count FROM summary WHERE conf_uid=?", ("smoke_a",)).fetchone()[0]
conn.close()
check("stats == raw SQL", (raw_a, raw_d, raw_k, raw_t) ==
      (s2["active_memories"], s2["deprecated_memories"], s2["keywords"], s2["turn_count"]))

print("== 4. direct repository over a fresh provider (same DB) ==")
prov = SQLiteStorageProvider("smoke_a")
mem = MemoryRepository(prov)
mem2 = MemoryRepository(prov)
check("repo typed against protocol", MemoryRepository.__init__.__annotations__["provider"] is P2)
check("repo lists rows", len(mem.list_all_memories()) == 2)
check("repo row_to_memory_record", mem.get_memory(r1.memory_id).conf_uid == "smoke_a")
check("repo count", mem.count_memories("active") == 1)
check("repo keyword count", KeywordRepository(prov).count_keywords() == 1)
check("repo state", StateRepository(prov).get_state().current_topic == "火锅")
check("repo summary turn", SummaryRepository(prov).get_turn_count() == 9)
check("repo shared scope sees facade writes", mem.find_active_by_content("用户非常喜欢吃火锅") is not None)
prov.close()

p_iso = SQLiteStorageProvider("smoke_iso")
check("provider-scoped isolation", MemoryRepository(p_iso).list_all_memories() == [])
check("isolation keyword", KeywordRepository(p_iso).count_keywords() == 0)
check("isolation turn", SummaryRepository(p_iso).get_turn_count() == 0)
p_iso.close()

print("== 5. legacy DB compatibility (table created by pre-refactor raw SQL) ==")
legacy_uid = "legacy_conf"
legacy_dir = os.path.join(os.getcwd(), "long_term_memory_data")
os.makedirs(legacy_dir, exist_ok=True)
legacy_path = os.path.join(legacy_dir, f"{legacy_uid}.db")
lconn = sqlite3.connect(legacy_path)
lconn.executescript("""
CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY, conf_uid TEXT NOT NULL, memory_type TEXT NOT NULL,
    content TEXT NOT NULL, keywords TEXT NOT NULL DEFAULT '[]',
    source_history_uid TEXT DEFAULT '', importance REAL DEFAULT 0.5,
    confidence REAL DEFAULT 0.8, status TEXT DEFAULT 'active',
    use_count INTEGER DEFAULT 0, created_at REAL, updated_at REAL,
    last_used_at REAL, history TEXT DEFAULT '[]');
CREATE INDEX IF NOT EXISTS idx_mem_conf ON memories(conf_uid, status);
CREATE TABLE IF NOT EXISTS keywords (
    keyword TEXT NOT NULL, category TEXT NOT NULL, conf_uid TEXT NOT NULL,
    hit_count INTEGER DEFAULT 1, first_seen_at REAL, last_seen_at REAL,
    PRIMARY KEY (keyword, category, conf_uid));
CREATE TABLE IF NOT EXISTS user_state (
    conf_uid TEXT PRIMARY KEY, emotion TEXT, energy REAL, stress REAL,
    current_topic TEXT, intent TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS summary (
    conf_uid TEXT PRIMARY KEY, conversation_summary TEXT DEFAULT '',
    turn_count INTEGER DEFAULT 0, updated_at REAL);
""")
lconn.execute(
    "INSERT INTO memories (memory_id, conf_uid, memory_type, content, keywords,"
    " importance, confidence, status, use_count, created_at, updated_at, last_used_at, history)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
    ("legacy-1", legacy_uid, "preference", "用户爱喝茶", '["茶"]', 0.9, 0.9,
     "active", 3, 1700000000.0, 1700000001.0, 0.0, "[]"),
)
lconn.execute(
    "INSERT INTO summary (conf_uid, conversation_summary, turn_count, updated_at) VALUES (?,?,?,?)",
    (legacy_uid, "旧摘要", 7, 1700000002.0),
)
lconn.commit()
lconn.close()

lstore = MemoryStore(legacy_uid)
lrec = lstore.get_memory("legacy-1")
check("legacy row readable", lrec is not None and lrec.content == "用户爱喝茶")
check("legacy keywords json parsed", lrec.keywords == ["茶"])
check("legacy use_count preserved", lrec.use_count == 3)
check("legacy summary readable", lstore.get_summary() == "旧摘要")
check("legacy turn_count preserved", lstore.get_turn_count() == 7)
lstore.add_memory(MemoryRecord.new(legacy_uid, "fact", "新事实", ["新"], 0.5, 0.9))
check("legacy db writable", lstore.count_memories("active") == 2)
check("legacy bump", lstore.bump_turn_count() == 8)
lstore.close()

print("== 6. concurrency ==")
cconf = "smoke_conc"
cstore = MemoryStore(cconf)
cstore.upsert_keyword("seed", "fact")
cthread_errors = []
N_THREADS, N = 6, 25


def worker(tid):
    try:
        s = MemoryStore(cconf)
        for i in range(N):
            rec = MemoryRecord.new(cconf, "fact", f"t{tid}-r{i}", [f"t{tid}"], 0.5, 0.9)
            s.add_memory(rec)
            s.upsert_keyword(f"kw{tid}-{i}", "fact")
            s.bump_turn_count()
            s.list_memories(status="active", memory_type="fact")
            s.search_active([f"t{tid}"])
            s.get_state()
            s.stats()
        s.close()
    except Exception as e:  # noqa: BLE001
        cthread_errors.append(f"t{tid}: {e}")


threads = [threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
t0 = time.time()
for t in threads:
    t.start()
for t in threads:
    t.join()
dt = time.time() - t0
check("no thread errors", not cthread_errors, str(cthread_errors[:3]))
check("concurrent writes intact",
      cstore.count_memories("active") == N_THREADS * N,
      f"got {cstore.count_memories('active')} want {N_THREADS * N}")
check("concurrent turn_count intact", cstore.get_turn_count() == N_THREADS * N,
      f"got {cstore.get_turn_count()}")
check("concurrent keywords intact", len(cstore.list_keywords(limit=1000)) == N_THREADS * N + 1,
      f"got {len(cstore.list_keywords(limit=1000))}")
print(f"       ({N_THREADS} threads x {N} iters in {dt:.2f}s)")
cstore.close()

print("== 7. transaction atomicity / rollback ==")
p_tx = SQLiteStorageProvider("smoke_tx")
Before = MemoryRepository(p_tx).count_memories("active")


class Boom(RuntimeError):
    pass


try:
    with p_tx.transaction() as tx:
        tx.execute(
            "INSERT INTO memories (memory_id, conf_uid, memory_type, content, keywords,"
            " importance, confidence, status, use_count, created_at, updated_at,"
            " last_used_at, history) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("tx-1", "smoke_tx", "fact", "should rollback", "[]", 0.5, 0.8,
             "active", 0, 1.0, 1.0, 0.0, "[]"),
        )
        tx.execute(
            "INSERT INTO memories (memory_id, conf_uid, memory_type, content, keywords,"
            " importance, confidence, status, use_count, created_at, updated_at,"
            " last_used_at, history) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("tx-2", "smoke_tx", "fact", "committed", "[]", 0.5, 0.8,
             "active", 0, 1.0, 1.0, 0.0, "[]"),
        )
        raise Boom("intentional")
except Boom:
    pass
After = MemoryRepository(p_tx).count_memories("active")
check("rollback discards whole transaction", After == Before, f"{Before} -> {After}")

try:
    with p_tx.transaction() as tx:
        tx.execute(
            "INSERT INTO memories (memory_id, conf_uid, memory_type, content, keywords,"
            " importance, confidence, status, use_count, created_at, updated_at,"
            " last_used_at, history) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("tx-ok", "smoke_tx", "fact", "good", "[]", 0.5, 0.8,
             "active", 0, 1.0, 1.0, 0.0, "[]"),
        )
        mid = tx.query_one("SELECT content FROM memories WHERE memory_id=?", ("tx-ok",))
        check("read-your-write inside tx", mid is not None and mid[0] == "good")
except Exception as e:  # noqa: BLE001
    check("commit path raised", False, str(e))
check("commit persists", MemoryRepository(p_tx).count_memories("active") == Before + 1)

r = p_tx.execute("UPDATE memories SET use_count=use_count+1 WHERE conf_uid=?", ("smoke_tx",))
check("execute returns rowcount", r == Before + 1, str(r))
check("execute no-op rowcount", p_tx.execute(
    "UPDATE memories SET use_count=use_count+1 WHERE conf_uid=?", ("nobody",)) == 0)
check("query_one miss is None", p_tx.query_one(
    "SELECT * FROM memories WHERE memory_id=?", ("ghost",)) is None)
check("query_rows miss is empty", len(p_tx.query_rows(
    "SELECT * FROM memories WHERE conf_uid=?", ("ghost",))) == 0)
p_tx.close()

print("== 8. exception paths ==")
closed = MemoryStore("smoke_closed")
closed.provider.close()
try:
    closed.stats()
    check("closed provider raises", False, "no exception")
except Exception as e:  # noqa: BLE001
    check("closed provider raises", True, type(e).__name__)

try:
    p_bad = SQLiteStorageProvider("smoke_tx")
    with p_bad.transaction() as tx:
        tx.execute("INSERT INTO nope (a) VALUES (1)")
    check("bad SQL raises inside tx", False, "no exception")
except Exception as e:  # noqa: BLE001
    check("bad SQL raises inside tx", True, type(e).__name__)
finally:
    try:
        p_bad.close()
    except Exception:  # noqa: BLE001
        pass

uids = MemoryStore("smoke_paths")
check("db file created on disk", os.path.exists(uids.db_path))
uids.close()

print("== 9. architecture contract (source-level, static) ==")
REPO_FILES = [
    "storage/memory_repository.py",
    "storage/state_repository.py",
    "storage/summary_repository.py",
    "storage/keyword_repository.py",
]
PRIVATE = re.compile(r"\._lock\b|\._conn\b")
CONCRETE = re.compile(r"SQLiteStorageProvider|import sqlite3|from \.sqlite_provider")

for rel in REPO_FILES:
    text = open(os.path.join(PKG, rel), encoding="utf-8").read()
    pr = PRIVATE.findall(text)
    ck = CONCRETE.findall(text)
    check(f"no private/sqlite access in {rel}", not pr and not ck, str(pr + ck))

facade_text = open(os.path.join(PKG, "store.py"), encoding="utf-8").read()
facade_priv = PRIVATE.findall(facade_text)
facade_code = re.sub(r'"""[\s\S]*?"""', "", facade_text)  # drop docstrings
check("facade has no private/raw-SQL access", not facade_priv, str(facade_priv))
check("facade has no raw SQL", "SELECT" not in facade_code and "INSERT" not in facade_code)
# the facade is the composition root: naming the concrete provider there is
# intended (a StorageFactory is explicitly forbidden by the phase spec).
check("facade is the only composition root naming the concrete provider",
      facade_text.count("SQLiteStorageProvider") == 3,  # import + __init__ call + (kw) import line
      str(facade_text.count("SQLiteStorageProvider")))

provider_text = open(os.path.join(PKG, "storage/provider.py"), encoding="utf-8").read()
check("provider.py has no sqlite3 import", not re.search(r"^\s*import sqlite3", provider_text, re.M))
check("provider.py imports typing + contextlib only",
      "from typing import" in provider_text and "from contextlib import" in provider_text)
check("provider.py declares both Protocols",
      "class StorageProvider(Protocol)" in provider_text
      and "class StorageTransaction(Protocol)" in provider_text)
check("providers are runtime_checkable",
      provider_text.count("@runtime_checkable") == 2)

FORBIDDEN = ["AbstractRepository", "BaseRepository", "GenericRepository", "StorageFactory"]
all_src = ""
for root, _dirs, files in os.walk(PKG):
    for f in files:
        if f.endswith(".py"):
            all_src += open(os.path.join(root, f), encoding="utf-8").read()
check("no forbidden abstraction classes", not any(t in all_src for t in FORBIDDEN),
      str([t for t in FORBIDDEN if t in all_src]))

print("== 10. runtime schema identity vs pre-refactor implementation ==")
OLDPKG = os.path.join(WORK, "old_ltm")
os.makedirs(OLDPKG, exist_ok=True)
open(os.path.join(OLDPKG, "__init__.py"), "w").close()
shutil.copy(os.path.join(REMOTE, "store.py"), os.path.join(OLDPKG, "store.py"))
shutil.copy(os.path.join(REMOTE, "schemas.py"), os.path.join(OLDPKG, "schemas.py"))
sys.path.insert(0, WORK)
from old_ltm.store import MemoryStore as OldStore  # noqa: E402

old_store = OldStore("ddl_old")
new_prov = SQLiteStorageProvider("ddl_new")


def runtime_schema(db_path):
    conn = sqlite3.connect(db_path)
    try:
        objects = {
            (name, typ): " ".join((sql or "").split())
            for name, typ, sql in conn.execute(
                "SELECT name, type, sql FROM sqlite_master WHERE sql IS NOT NULL")
        }
        cols = {
            t: [(r[1], (r[2] or "").upper(), r[3], r[5]) for r in
                conn.execute(f"PRAGMA table_info({t})")]
            for (t, typ) in objects if typ == "table"
        }
        return objects, cols
    finally:
        conn.close()


old_obj, old_cols = runtime_schema(old_store.db_path)
new_obj, new_cols = runtime_schema(new_prov.db_path)
old_store.close()
new_prov.close()

check("same object set (tables+indexes)", set(old_obj) == set(new_obj),
      f"\n  old={sorted(old_obj)}\n  new={sorted(new_obj)}")
check("4 tables + 1 index", len(old_obj) == 5, str(sorted(old_obj)))

sql_mismatch = {k: (old_obj[k], new_obj.get(k)) for k in old_obj
                if " ".join(old_obj[k].lower().split()) !=
                " ".join((new_obj.get(k) or "").lower().split())}
check("CREATE statements identical (case/whitespace-insensitive)", not sql_mismatch,
      str(sql_mismatch))
check("column definitions identical (name/type/notnull/pk)", old_cols == new_cols,
      f"\n  old={old_cols}\n  new={new_cols}")

print("\n================ SMOKE SUMMARY ================")
print(f"RESULT: {ok} passed, {fail} failed")
if errors:
    print("failed checks:")
    for e in errors:
        print("  -", e)
print(f"workdir: {WORK}")
sys.exit(0 if fail == 0 else 1)
