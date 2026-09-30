"""E2E test: real WebSocket chat through the live server, verifying the
full long-term-memory pipeline (injection -> reply -> extraction -> recall).

Run ON THE REMOTE HOST:
  cd /home/uu/桌面/Open-LLM-VTuber
  /root/.local/bin/uv run python /tmp/ltm_e2e.py

Simulates the real frontend protocol:
  connect -> create-new-history -> text-input turns; on each "audio" payload
  replies "frontend-playback-complete" so the turn can finalize (this is what
  unblocks the LTM extraction task).

Checks (spec acceptance scenarios):
  A. preference stored & recalled (火锅)
  B. pet/person memory (小白)
  C. conflict: coffee -> 不喝咖啡, old deprecated w/ history
  D. keywords categorized (Python/网络安全/实习)
  E. transient chatter (下雨) produces no memory
  F. nickname (小雪) becomes relationship memory, recalled without keywords
"""

import asyncio
import json
import sys
import urllib.request

import websockets

BASE = "ws://127.0.0.1:12395/client-ws"
API = "http://127.0.0.1:12395/memory/api"

PASS = FAIL = 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


def api_get(path: str):
    with urllib.request.urlopen(API + path, timeout=15) as r:
        return json.loads(r.read().decode())


def api_json(method: str, path: str, body: dict = None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


async def drain(ws, settle: float = 0.5):
    """Read pending messages until quiet for `settle` seconds."""
    msgs = []
    try:
        while True:
            m = await asyncio.wait_for(ws.recv(), timeout=settle)
            msgs.append(json.loads(m) if isinstance(m, str) else m)
    except asyncio.TimeoutError:
        pass
    return msgs


async def chat_once(ws, text: str, timeout: float = 90.0) -> str:
    """Send text-input; ack audio payloads; return assistant full reply.

    The real reply text arrives inside each "audio" payload's display_text
    dict (the "full-text" channel only carries a "Thinking..." placeholder
    while the turn is streaming). The turn is complete when
    "force-new-message" arrives (after frontend-playback-complete) — that
    is also when LTM extraction gets scheduled, so we keep reading a bit
    longer to let server-side tasks settle.
    """
    await ws.send(json.dumps({"type": "text-input", "text": text}))
    full = ""
    audio_seen = 0
    force_new_seen = False
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if force_new_seen:
            await asyncio.sleep(0.3)
            break
        try:
            m = await asyncio.wait_for(ws.recv(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        try:
            d = json.loads(m)
        except Exception:
            continue
        t = d.get("type")
        if t == "audio":
            audio_seen += 1
            # per-sentence reply text lives here, not in "full-text"
            dt = d.get("display_text") or {}
            if isinstance(dt, dict) and dt.get("text"):
                full += dt["text"]
            # simulate the frontend finishing playback immediately
            await ws.send(json.dumps({"type": "frontend-playback-complete"}))
        elif t == "full-text":
            txt = d.get("text", "")
            # fallback only — avoid duplicating audio-captured text
            if txt and txt != "Thinking..." and not full:
                full += txt
        elif t == "force-new-message":
            force_new_seen = True
    return full.strip()


async def main() -> int:
    print("Connecting", BASE)
    async with websockets.connect(BASE, max_size=10 * 1024 * 1024) as ws:
        init = await drain(ws, 1.0)
        conf_uid = None
        for d in init:
            if d.get("type") == "set-model-and-conf":
                conf_uid = d["conf_uid"]
        print("conf_uid =", conf_uid)
        check("connected & got conf_uid", conf_uid is not None)

        # real frontend creates a history session before chatting
        await ws.send(json.dumps({"type": "create-new-history"}))
        hist = await drain(ws, 1.0)
        hist_uid = None
        for d in hist:
            if d.get("type") == "new-history-created":
                hist_uid = d["history_uid"]
        print("history_uid =", hist_uid)
        check("history session created", hist_uid is not None)

        # clean slate
        sessions = await asyncio.get_event_loop().run_in_executor(
            None, lambda: api_get("/sessions")
        )
        print("sessions:", sessions)
        if conf_uid in (sessions.get("conf_uids") or []):
            api_json("POST", "/wipe", {"confirm": "DELETE", "conf_uid": conf_uid})
            print("wiped existing memories")

        # --- A. preference: 火锅 --------------------------------------
        print("\n--- A. 火锅 preference ---")
        r = await chat_once(ws, "我最喜欢吃火锅了，特别是麻辣牛肉锅")
        print(f"reply[:60]: {r[:60]}")
        await asyncio.sleep(6)  # let fire-and-forget extraction finish
        mems = api_get(f"/memories?conf_uid={conf_uid}&status=all")
        hot = [m for m in mems["memories"] if "火锅" in m["content"]]
        check("A1 火锅 memory stored", len(hot) >= 1, str([m['content'] for m in mems['memories']]))

        r2 = await chat_once(ws, "我饿了，晚饭吃什么好？")
        print(f"reply[:80]: {r2[:80]}")
        await asyncio.sleep(4)
        used_ok = any(
            m.get("use_count", 0) >= 1
            for m in api_get(f"/memories?conf_uid={conf_uid}&status=all")["memories"]
        )
        check("A2 memory used (retrieval hit)", used_ok)

        # --- B. pet: 小白 ----------------------------------------------
        print("\n--- B. 小白 the cat ---")
        r = await chat_once(ws, "我家养了一只猫，名字叫小白，特别可爱")
        print(f"reply[:60]: {r[:60]}")
        await asyncio.sleep(6)
        mems = api_get(f"/memories?conf_uid={conf_uid}&status=all")
        cat = [m for m in mems["memories"] if "小白" in m["content"]]
        check("B1 小白 memory stored", len(cat) >= 1, str([m['content'] for m in mems['memories']]))

        # --- C. conflict: coffee --------------------------------------
        print("\n--- C. coffee conflict ---")
        r = await chat_once(ws, "我最近迷上了喝咖啡，每天一杯拿铁")
        print(f"reply[:60]: {r[:60]}")
        await asyncio.sleep(6)
        r = await chat_once(ws, "对了说起来，我现在不喝咖啡了，改喝茶了")
        print(f"reply[:60]: {r[:60]}")
        await asyncio.sleep(6)
        mems = api_get(f"/memories?conf_uid={conf_uid}&status=all")
        all_coffee = [m for m in mems["memories"] if "咖啡" in m["content"]]
        active_coffee = [m for m in all_coffee if m["status"] == "active"]
        deprecated_coffee = [m for m in all_coffee if m["status"] == "deprecated"]
        check(
            "C1 old coffee deprecated",
            len(deprecated_coffee) >= 1,
            str([(m['content'], m['status']) for m in all_coffee]),
        )
        check(
            "C2 deprecated keeps history",
            len(deprecated_coffee) > 0 and all(m.get("history") for m in deprecated_coffee),
        )
        check(
            "C3 active coffee is new version",
            any("不喝" in m["content"] or "茶" in m["content"] for m in active_coffee),
            str([(m['content'], m['status']) for m in all_coffee]),
        )

        # --- D. keyword categories ------------------------------------
        print("\n--- D. keyword categories ---")
        r = await chat_once(ws, "我在学Python和网络安全，准备找实习")
        print(f"reply[:60]: {r[:60]}")
        await asyncio.sleep(6)
        kws = api_get(f"/keywords?conf_uid={conf_uid}")
        kmap = {k["keyword"]: k["category"] for k in kws["keywords"]}
        print("keywords:", kmap)
        check("D1 Python->technology", kmap.get("Python") == "technology", str(kmap))
        check("D2 网络安全->skill", kmap.get("网络安全") == "skill", str(kmap))
        check("D3 实习->goal", kmap.get("实习") == "goal", str(kmap))

        # --- E. transient chatter: no memory --------------------------
        print("\n--- E. 下雨 (no memory) ---")
        r = await chat_once(ws, "今天下雨了，好烦啊")
        print(f"reply[:60]: {r[:60]}")
        await asyncio.sleep(6)
        mems = api_get(f"/memories?conf_uid={conf_uid}&status=all")
        rain = [m for m in mems["memories"] if "下雨" in m["content"]]
        check("E1 下雨 no long-term memory", len(rain) == 0, str([m['content'] for m in rain]))

        # --- F. nickname: 小雪 ----------------------------------------
        print("\n--- F. nickname 小雪 ---")
        r = await chat_once(ws, "以后你可以叫我小雪")
        print(f"reply[:60]: {r[:60]}")
        await asyncio.sleep(6)
        mems = api_get(f"/memories?conf_uid={conf_uid}&status=all")
        nick = [m for m in mems["memories"] if "小雪" in m["content"]]
        check("F1 小雪 memory stored", len(nick) >= 1, str([(m['memory_type'], m['content']) for m in mems['memories']]))
        check(
            "F2 nickname is relationship/identity type",
            any(m["memory_type"] in ("relationship", "identity") for m in nick),
            str([(m['memory_type'], m['content']) for m in nick]),
        )

        print("\n--- F-recall. ask nickname (no keyword help) ---")
        r = await chat_once(ws, "你还记得我叫什么名字吗？")
        print(f"reply[:100]: {r[:100]}")
        check("F3 nickname recalled in reply", "小雪" in r, r[:120])

        # final debug snapshot
        debug = api_get(f"/debug?conf_uid={conf_uid}")
        print("\nfinal stats:", json.dumps(debug.get("stats", {}), ensure_ascii=False))

    print(f"\n{'='*50}\nE2E RESULT: {PASS} passed, {FAIL} failed\n{'='*50}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
