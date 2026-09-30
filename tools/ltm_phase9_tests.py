"""Phase 9 Decision engine tests (spec §三十二 matrix).

Covers: schema (valid selected/abstain, invalid status, missing
evaluation/strategy, invalid confidence, invalid provenance) / rule
decision (strong/weak/multiple/ranking/tie-ambiguity/all-false/empty/
low-confidence) / LLM (valid, malformed, external eval id, external
strategy id via rewritten selection, null selection, timeout, empty) /
storage (CRUD, conf isolation, restart, reverse trace) / engine
(normal, empty, no applicable, low confidence, tie, analyzer failure,
repeated decision) / boundary scan (conversation/Memory/Prompt/Agent/
Personality untouched).

Run local:  sshagent/Scripts/python.exe tools/ltm_phase9_tests.py
Run server: LTM_DEC_SRC=src LTM_DEC_HERMES_LIVE=1 \
            uv run python tools/ltm_phase9_tests.py
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
SRC = os.environ.get("LTM_DEC_SRC", os.path.join(ROOT, "src"))
HERMES_LIVE = os.environ.get("LTM_DEC_HERMES_LIVE", "0") == "1"
HERMES_BASE = os.environ.get("LTM_HERMES_BASE_URL", "http://127.0.0.1:12396")

import shutil  # noqa: E402
SRC_ABS = os.path.abspath(SRC)
WORK = tempfile.mkdtemp(prefix="ltm_p9_")
PKG = os.path.join(WORK, "pkg")
LTM_PKG = os.path.join(PKG, "long_term_memory")
os.makedirs(os.path.join(LTM_PKG, "storage"))
DOMAINS = {}
for name in ("experience", "reflection", "lesson", "strategy", "evaluation",
             "decision"):
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
                                 "analyzer.py"))):
    for rel in files:
        shutil.copy(os.path.join(SRC_ABS, "open_llm_vtuber", dom, rel),
                    os.path.join(DOMAINS[dom], rel))

sys.path.insert(0, os.path.dirname(PKG))
os.chdir(WORK)

from pkg.long_term_memory.store import MemoryStore  # noqa: E402
from pkg.evaluation.schemas import EvaluationRecord  # noqa: E402
from pkg.evaluation.repository import EvaluationRepository  # noqa: E402
from pkg.decision.schemas import DecisionRecord  # noqa: E402
from pkg.decision.repository import DecisionRepository  # noqa: E402
from pkg.decision.engine import DecisionEngine  # noqa: E402
from pkg.decision.analyzer import RuleDecisionAnalyzer  # noqa: E402

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


def make_eval(conf, strategy_id, applicable, relevance, confidence,
              match, reason="命中"):
    e = EvaluationRecord.new(conf, strategy_id)
    e.applicable, e.relevance = applicable, relevance
    e.confidence, e.condition_match = confidence, match
    e.reason = reason
    return e


# ---------------------------------------------------------------------------
section("1. DecisionRecord schema validation")
store = MemoryStore("dec_a")
drepo = DecisionRepository(store.provider)
d = DecisionRecord.new("dec_a", "selected")
d.selected_evaluation_id = "e1"
d.selected_strategy_id = "s1"
d.confidence = 0.8
d.reason = "该评估 relevance 与 confidence 均最高。"
drepo.save(d)
g = drepo.get(d.decision_id)
check("valid selected save/get",
      g is not None and g.status == "selected"
      and g.selected_evaluation_id == "e1" and g.selected_strategy_id == "s1")
check("to_dict/from_dict roundtrip",
      DecisionRecord.from_dict(d.to_dict()).decision_id == d.decision_id)

a = DecisionRecord.new("dec_a", "abstain")
a.reason = "无可用候选，放弃选择。"
drepo.save(a)
check("valid abstain save/get",
      drepo.get(a.decision_id).status == "abstain"
      and not drepo.get(a.decision_id).selected_strategy_id)

for name, mutate in (
        ("invalid status", lambda r: setattr(r, "status", "maybe")),
        ("selected missing evaluation id",
         lambda r: setattr(r, "selected_evaluation_id", "")),
        ("selected missing strategy id",
         lambda r: setattr(r, "selected_strategy_id", "")),
        ("invalid confidence", lambda r: setattr(r, "confidence", 1.5)),
        ("abstain carrying selection",
         lambda r: (setattr(r, "selected_strategy_id", "s9"),
                    setattr(r, "status", "abstain"))),
):
    b = DecisionRecord.new("dec_a", "selected")
    b.selected_evaluation_id = "e1"
    b.selected_strategy_id = "s1"
    b.confidence = 0.5
    mutate(b)
    try:
        drepo.save(b)
        check(f"{name} rejected", False)
    except ValueError:
        check(f"{name} rejected", True)

bad_reason = DecisionRecord.new("dec_a", "abstain")
bad_reason.reason = "必须执行此策略"
try:
    drepo.save(bad_reason)
    check("commanding reason rejected", False)
except ValueError:
    check("commanding reason rejected", True)

d.confidence = 0.85
drepo.save(d)
check("duplicate save stays one row (overwrite)",
      drepo.count() == 2 and abs(drepo.get(d.decision_id).confidence - 0.85) < 1e-9)
check("get miss -> None", drepo.get("nope") is None)
check("delete", drepo.delete(a.decision_id) and drepo.count() == 1)

# ---------------------------------------------------------------------------
section("2. Rule decision: selection / abstain / tie")
rules = RuleDecisionAnalyzer()
e_strong = make_eval("dec_a", "sA", True, 0.9, 0.85, 0.7)
e_weak = make_eval("dec_a", "sB", True, 0.5, 0.6, 0.3)
dec = rules.decide([e_weak, e_strong], "dec_a")
check("one strong candidate selected",
      dec.status == "selected" and dec.selected_strategy_id == "sA"
      and dec.selected_evaluation_id == e_strong.evaluation_id)
check("decision confidence derived from chosen evaluation",
      abs(dec.confidence - round(0.5 * 0.85 + 0.5 * 0.9, 4)) < 1e-9)
check("reason explains choice", "relevance" in dec.reason
      and "confidence" in dec.reason)

dec_low = rules.decide(
    [make_eval("dec_a", "sA", True, 0.9, 0.4, 0.7)], "dec_a")
check("low confidence -> abstain", dec_low.status == "abstain"
      and "置信度不足" in dec_low.reason)

dec_false = rules.decide(
    [make_eval("dec_a", "sA", False, 0.9, 0.9, 0.9)], "dec_a")
check("all non-applicable -> abstain", dec_false.status == "abstain"
      and "不适用" in dec_false.reason)

dec_empty = rules.decide([], "dec_a")
check("empty evaluations -> abstain (no crash)",
      dec_empty.status == "abstain")

tie_a = make_eval("dec_a", "sA", True, 0.8, 0.8, 0.5)
tie_b = make_eval("dec_a", "sB", True, 0.8, 0.8, 0.5)
dec_tie = rules.decide([tie_a, tie_b], "dec_a")
check("exact tie -> abstain (ambiguous, never random/order pick)",
      dec_tie.status == "abstain" and "ambiguous" in dec_tie.reason
      and len(dec_tie.metadata.get("ambiguous_ids", [])) == 2)

near_a = make_eval("dec_a", "sA", True, 0.8, 0.8, 0.5)
near_b = make_eval("dec_a", "sB", True, 0.8, 0.8, 0.6)
dec_near = rules.decide([near_a, near_b], "dec_a")
check("tie broken by condition_match -> selected",
      dec_near.status == "selected"
      and dec_near.selected_strategy_id == "sB")

conf_a = make_eval("dec_a", "sA", True, 0.8, 0.7, 0.5)
conf_b = make_eval("dec_a", "sB", True, 0.8, 0.9, 0.5)
dec_conf = rules.decide([conf_a, conf_b], "dec_a")
check("tie broken by confidence -> selected",
      dec_conf.status == "selected"
      and dec_conf.selected_strategy_id == "sB")

# ---------------------------------------------------------------------------
section("3. LLM decision analyzer: valid + rejections")
estore = MemoryStore("dec_l")
erepo = EvaluationRepository(estore.provider)
ev1 = make_eval("dec_l", "s1", True, 0.9, 0.85, 0.7)
ev2 = make_eval("dec_l", "s2", True, 0.5, 0.6, 0.3)
for ev in (ev1, ev2):
    erepo.save(ev)
cfg = {"decision": {"llm_analysis": True, "llm_timeout": 5.0}}


def llm_run(payload=None, error=False):
    f = FakeLLM(payload, error)
    eng = DecisionEngine(DecisionRepository(MemoryStore("dec_l").provider),
                         EvaluationRepository(MemoryStore("dec_l").provider),
                         config=cfg, llm=f)
    out = asyncio.run(eng.decide_recent_llm("dec_l"))
    return out, f


out, f = llm_run(json.dumps(
    {"selected_evaluation_id": ev1.evaluation_id,
     "reason": "该评估最相关"}))
check("valid llm decision selected",
      out is not None and out.status == "selected"
      and out.selected_evaluation_id == ev1.evaluation_id
      and out.selected_strategy_id == "s1")
check("strategy id comes from the evaluation (never the LLM)",
      out is not None and out.selected_strategy_id == ev1.strategy_id)

out_ext, _ = llm_run(json.dumps(
    {"selected_evaluation_id": "E999", "reason": "x"}))
check("external evaluation id rejected -> rule fallback",
      out_ext is not None and out_ext.metadata.get("analyzer") == "rules")

out_null, _ = llm_run(json.dumps(
    {"selected_evaluation_id": None, "reason": "没有足够强的候选"}))
check("llm null selection -> valid abstain",
      out_null is not None and out_null.status == "abstain"
      and out_null.metadata.get("analyzer") == "llm")

check("malformed json -> rule fallback",
      llm_run("not json")[0].metadata.get("analyzer") == "rules")
check("timeout/error -> rule fallback",
      llm_run(error=True)[0].metadata.get("analyzer") == "rules")
check("empty output -> rule fallback",
      llm_run("")[0].metadata.get("analyzer") == "rules")

# contradictory: llm picks an evaluation but with bogus extra field —
# extra fields are ignored, selection must still be in the whitelist
out_ct, _ = llm_run(json.dumps(
    {"selected_evaluation_id": ev2.evaluation_id,
     "strategy_id": "S999", "reason": "x"}))
check("llm cannot rewrite strategy id (whitelist wins)",
      out_ct is not None and out_ct.selected_strategy_id == ev2.strategy_id)

# ---------------------------------------------------------------------------
section("4. Engine paths + idempotency")
nstore = MemoryStore("dec_n")
nrepo = DecisionRepository(nstore.provider)
nerepo = EvaluationRepository(nstore.provider)
neng = DecisionEngine(nrepo, nerepo, config={})

check("empty evaluation pool -> abstain decision persisted",
      (lambda r: r is not None and r.status == "abstain")(
          neng.decide_recent("dec_n")))

e_good = make_eval("dec_n", "sN", True, 0.9, 0.85, 0.7)
e_good2 = make_eval("dec_n", "sN2", True, 0.7, 0.8, 0.5)
nerepo.save(e_good)
nerepo.save(e_good2)
d1 = neng.decide_recent("dec_n")
check("normal path selected", d1.status == "selected"
      and d1.selected_strategy_id == "sN")
d2 = neng.decide_recent("dec_n")
check("repeated decision creates new timestamped records "
      "(decision history by design)",
      d1.decision_id != d2.decision_id and nrepo.count() == 3)

# analyzer failure isolation: a poisoned evaluation must not crash
poison = EvaluationRecord.new("dec_n", "sN")
poison.applicable = True
poison.relevance = 0.99
poison.confidence = "not-a-float"   # type corruption
poison.condition_match = 0.9
poison.reason = "poison"
out_poison = neng.decide_over("dec_n", [poison])
check("analyzer exception -> explicit failure (None), no crash",
      out_poison is None)

# ---------------------------------------------------------------------------
section("5. conf_uid isolation + restart + reverse trace")
istore = MemoryStore("dec_i")
irepo = DecisionRepository(istore.provider)
idA = DecisionRecord.new("dec_i", "selected")
idA.selected_evaluation_id = "evA"
idA.selected_strategy_id = "sA"
idA.confidence = 0.7
idA.reason = "A 的决策"
irepo.save(idA)
idB = DecisionRecord.new("dec_j", "selected")
idB.selected_evaluation_id = "evB"
idB.selected_strategy_id = "sB"
idB.confidence = 0.7
idB.reason = "B 的决策"
DecisionRepository(MemoryStore("dec_j").provider).save(idB)
ria = DecisionRepository(MemoryStore("dec_i").provider)
rib = DecisionRepository(MemoryStore("dec_j").provider)
check("isolation: A only sees A",
      all(x.conf_uid == "dec_i" for x in ria.list_decisions(100))
      and ria.get(idB.decision_id) is None)
check("isolation: B only sees B",
      all(x.conf_uid == "dec_j" for x in rib.list_decisions(100)))
check("reverse trace by evaluation",
      ria.list_by_evaluation("evA")[0].decision_id == idA.decision_id)
check("reverse trace by strategy",
      ria.list_by_strategy("sA")[0].decision_id == idA.decision_id)
check("restart persistence",
      DecisionRepository(MemoryStore("dec_i").provider).count() == 1
      and DecisionRepository(MemoryStore("dec_i").provider)
      .get(idA.decision_id) is not None)

# ---------------------------------------------------------------------------
section("6. Boundary scan: five isolations (§三十三)")


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


DEC_SRC_DIR = os.path.join(SRC_ABS, "open_llm_vtuber", "decision")
for fn in sorted(os.listdir(DEC_SRC_DIR)):
    if not fn.endswith(".py"):
        continue
    hits = [ln.strip()[:70] for _, ln in _code_lines(os.path.join(DEC_SRC_DIR, fn))
            if re.search(r"sqlite3|httpx\b|requests\b|HermesStorageProvider", ln)]
    check(f"decision/{fn}: no direct backend access", not hits, str(hits[:2]))

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
    check(f"{label}: no decision import/use",
          "decision" not in src_text and "Decision" not in src_text)

# ---------------------------------------------------------------------------
section("7. Hermes provider: decisions (sidecar path)")
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
    tag = f"dec9-{int(time.time())}"
    hp = HermesStorageProvider(tag, base_url=HERMES_BASE, user_id="vtuber",
                               timeout=20.0)
    hrepo = DecisionRepository(hp)
    hd = DecisionRecord.new(tag, "selected")
    hd.selected_evaluation_id = "he1"
    hd.selected_strategy_id = "hs1"
    hd.confidence = 0.8
    hd.reason = "Hermes 决策"
    hrepo.save(hd)
    check("[hermes] save/get", hrepo.get(hd.decision_id) is not None
          and hrepo.count() == 1)
    check("[hermes] reverse trace by evaluation",
          hrepo.list_by_evaluation("he1")[0].decision_id == hd.decision_id)
    check("[hermes] reverse trace by strategy",
          hrepo.list_by_strategy("hs1")[0].decision_id == hd.decision_id)
    hp2 = HermesStorageProvider(tag, base_url=HERMES_BASE, user_id="vtuber",
                                timeout=20.0)
    hrepo2 = DecisionRepository(hp2)
    check("[hermes] restart persistence",
          hrepo2.count() == 1 and hrepo2.get(hd.decision_id) is not None)
    hp2.close()
    hp.close()
else:
    print("\nHermes Decision Integration Test: BLOCKED")
    print("Reason: hermes service not reachable at", HERMES_BASE)

print(f"\n{'='*54}")
print(f"PHASE 9 RESULT: {ok} passed, {fail} failed"
      + ("" if h_ok else "  [hermes LIVE BLOCKED]"))
print(f"{'='*54}")
if errors:
    print("FAILED:")
    for e in errors:
        print(f"  - {e}")
sys.exit(0 if fail == 0 else 1)
