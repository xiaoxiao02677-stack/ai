"""Verify the injected aggregator logic in isolation (extraction +
simulation), and E2E through the real WS: connect, request a chat
turn, and assert the client-side ordered playback semantics.

Part 1 (logic): extract window.__ccStream from the bundle and run it
against simulated start/chunk/chunk/end sequences, asserting the
legacy callback receives ONE concatenated payload per sequence, in
order, with all chunks assembled.

Part 2 (wire): hit the live backend WS with a streaming TTS message
flow (server-driven) — covered by ltm_stream_tests server-side; here
we only verify the bundle serves and the aggregator is present.
"""
import json
import re
import subprocess
import sys
import urllib.request

API = "http://127.0.0.1:12395"
ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    ok += 1 if cond else 0
    fail += 0 if cond else 1
    print(("[PASS] " if cond else "[FAIL] ") + name +
          ("" if cond else "  " + str(detail)[:100]))


# ---- Part 1: extract + simulate the aggregator ---------------------
bundle = open("frontend/assets/main-nu7uwxNJ.js",
              encoding="utf-8").read()
m = re.search(
    r"window\.__ccStream=\(function\(\)\{.*?\}\)\(\);",
    bundle, re.DOTALL)
check("1. aggregator present in bundle", m is not None)
if m:
    code = m.group(0)
    # run it in a JS-less way: reimplement the same state machine in
    # python is NOT a test of the JS. Instead run node if available,
    # else validate structure: balanced braces + key statements.
    try:
        r = subprocess.run(["node", "-e", code + """
const s = window.__ccStream;
// no-op legacy cb
let received = [];
const cb = (p) => received.push(p);
// simulate a streamed sentence: start, chunk0, chunk1, end
s.onMsg({stream:'start', sequence:0, chunks:2,
         display_text:{text:'hi'}, actions:null}, cb);
s.onMsg({stream:'chunk', sequence:0, chunk_index:0,
         audio:'AAA'}, cb);
s.onMsg({stream:'chunk', sequence:0, chunk_index:1,
         audio:'BBB'}, cb);
s.onMsg({stream:'end', sequence:0, volumes:[1,2]}, cb);
// non-stream message must pass through (returns false)
const passthrough = s.onMsg({type:'audio', audio:'XX'}, cb);
console.log(JSON.stringify({
  received: received,
  passthrough: passthrough}));
"""], capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            out = json.loads(r.stdout.strip().splitlines()[-1])
            check("1a. one legacy payload per streamed sentence",
                  len(out["received"]) == 1)
            check("1b. chunks concatenated IN ORDER (AAA+BBB)",
                  out["received"][0]["audioBase64"] == "AAABBB")
            check("1c. volumes/expressions forwarded",
                  out["received"][0]["volumes"] == [1, 2])
            check("1d. non-stream messages pass through untouched",
                  out["passthrough"] is False)
        else:
            check("1x. node execution of aggregator", False,
                  r.stderr[:120])
    except FileNotFoundError:
        # no node on this host: structural checks only
        check("1a. aggregator structure: start/chunk/end handlers",
              "start'" in code and "chunk'" in code
              and "end'" in code)
        check("1b. concatenation loop present",
              "for(var i=0;i<s.chunks.length;i++)" in code)
        check("1c. passthrough contract (returns false)",
              "return true;}" in code and "return false" not in code
              or "if(!m||!m.stream){return false;}" in code)

# ---- Part 2: bundle serves with the patch --------------------------
req = urllib.request.Request(
    API + "/assets/main-nu7uwxNJ.js")
with urllib.request.urlopen(req, timeout=15) as r:
    body = r.read().decode("utf-8", "replace")
check("2. patched bundle served by the backend",
      "__ccStream" in body and len(body) == len(bundle))

print(f"\n===== FE STREAM: {ok} passed, {fail} failed =====")
sys.exit(0 if fail == 0 else 1)
