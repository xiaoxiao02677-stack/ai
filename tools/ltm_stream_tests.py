#!/usr/bin/env python
"""Streaming verification: (1) edge_tts streamed synthesis first-chunk
latency vs whole-file latency; (2) TTSTaskManager streamed protocol
emits start/chunk/end in order; (3) ordering across sequences."""
import asyncio
import json
import sys
import time

sys.path.insert(0, "src")

from open_llm_vtuber.tts.edge_tts import TTSEngine  # noqa: E402

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    ok += 1 if cond else 0
    fail += 0 if cond else 1
    print(("[PASS] " if cond else "[FAIL] ") + name +
          ("" if cond else "  " + str(detail)[:100]))


async def main():
    engine = TTSEngine(voice="zh-CN-XiaoxiaoNeural")

    # 1) streamed synthesis: first chunk arrives early, file complete
    text = ("这是一段比较长的句子，用来验证流式合成的首块延迟，"
            "以及整句完成的时间。流式合成应该在第一块音频到达时"
            "就立刻返回时间戳，而不是等整句都合成完毕。")
    t0 = time.time()
    path, first_ms = await engine.async_generate_audio_streamed(
        text, "p26stream_probe")
    total_ms = int((time.time() - t0) * 1000)
    check("1. streamed synthesis returns first-chunk latency",
          path is not None and first_ms is not None
          and first_ms > 0, f"first={first_ms}ms")
    check("1b. first chunk arrives BEFORE the whole file",
          first_ms < total_ms,
          f"first={first_ms}ms total={total_ms}ms")
    import os
    check("1c. complete file on disk (non-zero, real mp3)",
          os.path.isfile(path) and os.path.getsize(path) > 1000)
    import subprocess
    r = subprocess.run(["ffprobe", "-v", "quiet", "-show_format",
                        "-of", "json", path], capture_output=True,
                       text=True)
    meta = json.loads(r.stdout or "{}")
    dur = float(meta.get("format", {}).get("duration", 0))
    check("1d. audio is real and decodable (ffprobe)", dur > 2.0,
          f"dur={dur}s")
    print(f"    [stream] first-chunk {first_ms}ms | "
          f"whole-file {total_ms}ms | audio {dur:.1f}s")

    # 2) the manager protocol: start/chunk/end, in order
    from open_llm_vtuber.conversations.tts_manager import \
        TTSTaskManager  # noqa: E402
    from open_llm_vtuber.agent.output_types import DisplayText  # noqa

    msgs = []

    async def fake_ws(msg):
        msgs.append(json.loads(msg))

    mgr = TTSTaskManager()
    dt = DisplayText(name="测试", text="你好")
    await mgr.speak(tts_text="第一句，流式验证。",
                    display_text=dt, actions=None,
                    live2d_model=None, tts_engine=engine,
                    websocket_send=fake_ws)
    await mgr.speak(tts_text="第二句，顺序验证。",
                    display_text=dt, actions=None,
                    live2d_model=None, tts_engine=engine,
                    websocket_send=fake_ws)
    # wait for both sequences to finish streaming
    for _ in range(600):
        if len([m for m in msgs if m.get("stream") == "end"]) >= 2:
            break
        await asyncio.sleep(0.1)
    starts = [m for m in msgs if m.get("stream") == "start"]
    chunks = [m for m in msgs if m.get("stream") == "chunk"]
    ends = [m for m in msgs if m.get("stream") == "end"]
    check("2. streamed protocol: start/chunk/end messages emitted",
          len(starts) == 2 and len(chunks) >= 2 and len(ends) == 2,
          f"starts={len(starts)} chunks={len(chunks)} "
          f"ends={len(ends)}")
    seq_order = [m["sequence"] for m in starts]
    check("2b. sentence ordering preserved (seq 0 before seq 1)",
          seq_order == sorted(seq_order), seq_order)
    for s in starts:
        my_chunks = [c for c in chunks
                     if c["sequence"] == s["sequence"]]
        idxs = [c["chunk_index"] for c in my_chunks]
        check(f"2c. seq {s['sequence']}: chunk indices ordered "
              f"0..{len(idxs)-1}",
              idxs == list(range(len(idxs))), idxs)
        check(f"2d. seq {s['sequence']}: chunk count matches meta",
              len(my_chunks) == s.get("chunks", -1),
              f"{len(my_chunks)} vs {s.get('chunks')}")
    print(f"\n===== STREAMING: {ok} passed, {fail} failed =====")
    sys.exit(0 if fail == 0 else 1)


asyncio.run(main())
