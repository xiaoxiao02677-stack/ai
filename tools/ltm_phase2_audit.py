"""Phase 2 FINAL audit: full keyword sweep over the LTM module.

Classifies every keyword hit as CODE (real code), doc (docstring) or
comment, so the acceptance report can quote exact numbers per layer.

Usage: python tools/ltm_phase2_audit.py [--src PATH_TO_MODULE]
"""
import argparse
import os
import sys

KEYWORDS = [
    "sql", "SELECT", "INSERT", "UPDATE", "DELETE", "CREATE TABLE",
    "sqlite", "sqlite3", "connection", "cursor", "execute",
    "executemany", "fetchone", "fetchall", "query_one", "query_rows",
    "transaction", "commit", "rollback",
]

FILES = [
    "manager.py", "store.py", "retriever.py", "schemas.py",
    "deduplicator.py", "extractor.py", "keyword_extractor.py",
    "prompt_builder.py", "privacy.py",
    "storage/provider.py", "storage/sqlite_provider.py",
    "storage/repository.py", "storage/__init__.py",
]

# word-boundary variants for short/ambiguous keywords (avoid matching
# 'update' inside 'update_memory', 'execute' inside prose, etc.)
WORD = {"sql", "sqlite", "sqlite3", "connection", "cursor", "commit",
        "transaction", "rollback"}


def kw_hit(line_lower: str):
    for kw in KEYWORDS:
        k = kw.lower()
        if k in WORD:
            import re
            if re.search(rf"\b{re.escape(k)}\b", line_lower):
                return kw
        else:
            if k in line_lower:
                return kw
    return None


def audit_file(path: str):
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    hits = []
    in_doc = False
    for i, line in enumerate(lines, 1):
        s = line.strip()
        lower = line.lower()
        # docstring state machine (triple double quotes only — module style)
        if not in_doc and (s.startswith('"""') or s.startswith("'''")):
            q = '"""' if s.startswith('"""') else "'''"
            in_doc = not (s.count(q) >= 2)
            kw = kw_hit(lower)
            if kw:
                hits.append((i, kw, "doc", s[:110]))
            continue
        if in_doc:
            if '"""' in s or "'''" in s:
                in_doc = False
            kw = kw_hit(lower)
            if kw:
                hits.append((i, kw, "doc", s[:110]))
            continue
        if s.startswith("#"):
            kw = kw_hit(lower)
            if kw:
                hits.append((i, kw, "comment", s[:110]))
            continue
        # string literal inside code: flagged CODE but shown for review
        kw = kw_hit(lower)
        if kw:
            hits.append((i, kw, "CODE", s[:110]))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "src_ltm"))
    args = ap.parse_args()

    total_code = 0
    for rel in FILES:
        p = os.path.join(args.src, rel)
        if not os.path.exists(p):
            print(f"### {rel}: MISSING")
            continue
        hits = audit_file(p)
        code = [h for h in hits if h[2] == "CODE"]
        total_code += len(code)
        print(f"### {rel}: CODE={len(code)} doc/comment={len(hits) - len(code)}")
        for h in code:
            print(f"  CODE L{h[0]} [{h[1]}] {h[3]}")
        for h in hits:
            if h[2] != "CODE":
                print(f"  {h[2]:7s} L{h[0]} [{h[1]}] {h[3]}")
    print(f"\nTOTAL real-code keyword hits outside classification: {total_code}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
