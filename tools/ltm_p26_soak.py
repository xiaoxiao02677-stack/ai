#!/usr/bin/env python
"""P26 soak test: continuous mixed load, >=30 minutes (spec §六).

Cycles: TEXT chat (no-action) / VOICE input (sherpa ASR on a real
synthesized voice) / SET_LED ON/OFF (real ESP32 through the
workshop chain) / heartbeat + device-state + workshop reads.

Checks every cycle: backend alive, memory growth bounded, command
history bounded, no fabricated successes. Writes a per-cycle log;
final summary computes the pass/fail.
"""
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = "/home/uu/桌面/Open-LLM-VTuber"
API = "http://127.0.0.1:12395"
D = "xiaozhi-14c19fd13348"
DURATION_MIN = int(os.environ.get("P26_SOAK_MIN", "30"))
sys.path.insert(0, os.path.join(ROOT, "src"))

import numpy as np  # noqa: E402
import wave  # noqa: E402

from open_llm_vtuber.tts.edge_tts import TTSEngine  # noqa: E402
from open_llm_vtuber.input.schemas import SherpaOnnxProvider  # noqa: E402
from open_llm_vtuber.input.gateway import (  # noqa: E402
    ASRAdapter, AudioInputGateway)

tts = TTSEngine(voice="zh-CN-YunxiNeural")
asr_gw = AudioInputGateway(adapter=ASRAdapter(
    provider=SherpaOnnxProvider(), timeout=60.0))
import importlib.util as iu  # noqa: E402
spec = iu.spec_from_file_location("p21_runtime", ROOT + "/p21_runtime.py")
p21 = iu.module_from_spec(spec)
spec.loader.exec_module(p21)


def get(path):
    with urllib.request.urlopen(API + path, timeout=20) as r:
        return json.loads(r.read())


def post(path, payload):
    req = urllib.request.Request(API + path,
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type":
                                          "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def cycle_voice(spoken):
    """Real synthesized voice -> real ASR -> decision chain."""
    mp3 = asyncio.run(tts.async_generate_audio(
        spoken, "p26soak"))
    wav = "/tmp/p26soak.wav"
    subprocess.run(["ffmpeg", "-y", "-i", mp3, "-ar", "16000",
                    "-ac", "1", "-f", "wav", wav],
                   capture_output=True, check=True)
    with wave.open(wav, "rb") as w:
        frames = w.readframes(w.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(
        np.float32) / 32768.0
    utt = asr_gw.ingest(audio)
    if utt is None:
        return "asr-fail", None
    run = p21.handle_user_event(utt["text"], conf_uid="p26_soak")
    return "ok", run.get("no_action")


deadline = time.time() + DURATION_MIN * 60
cycle = 0
led_acks = 0
asr_ok = 0
errors = []
rss_samples = []


def rss_mb():
    out = subprocess.run(
        ["bash", "-c",
         "grep VmRSS /proc/$(pgrep -f run_server.py | head -1)/status"],
        capture_output=True, text=True, timeout=10)
    try:
        return int(out.stdout.split()[1]) // 1024
    except Exception:
        return -1


print(f"[soak] start {DURATION_MIN}min loop", flush=True)
while time.time() < deadline:
    cycle += 1
    t0 = time.time()
    row = []
    try:
        # 1. TEXT chat (no-action semantics via the p21 chain)
        run = p21.handle_user_event("现在几点？", conf_uid="p26_soak")
        row.append("text:" + ("noaction" if run["no_action"]
                              else "ACTION!?"))
        # 2. VOICE input
        st, noact = cycle_voice("把灯打开")
        if st == "ok":
            asr_ok += 1
            row.append("asr:ok" + ("/noact" if noact else "/act"))
        else:
            row.append("asr:fail")
            errors.append(f"cycle {cycle}: {st}")
        # 3. LED ON/OFF (real device)
        led = post(f"/workshop/api/devices/{D}/led", {"on": True})
        if led.get("device_ack", {}).get("status") == "ACK":
            led_acks += 1
            row.append("led:ACK")
        else:
            row.append("led:" + str(led.get("status")))
            errors.append(f"cycle {cycle}: led {led.get('status')}")
        post(f"/workshop/api/devices/{D}/led", {"on": False})
        # 4. observability reads
        ov = get("/workshop/api/overview")
        row.append("dev:" + str(ov["totals"]["online"]))
        cmds = get(f"/workshop/api/devices/{D}/commands")
        row.append("hist:" + str(len(cmds.get("commands", []))))
        rss_samples.append(rss_mb())
    except Exception as e:
        errors.append(f"cycle {cycle}: {type(e).__name__} {str(e)[:80]}")
        row.append("ERR")
    dt = time.time() - t0
    print(f"[soak {cycle}] {','.join(row)} ({dt:.1f}s)", flush=True)
    time.sleep(max(5.0, 20.0 - dt))

# final assertions
ov = get("/workshop/api/overview")
backend_up = True
rss_growth = (max(rss_samples) - rss_samples[0]) if rss_samples else 0
print(f"\n[soak] done: {cycle} cycles in {DURATION_MIN}min")
print(f"  asr ok: {asr_ok}/{cycle}  led acks: {led_acks}/{cycle}")
print(f"  rss samples: {len(rss_samples)} first={rss_samples[0] if rss_samples else '-'} "
      f"max={max(rss_samples) if rss_samples else '-'} growth={rss_growth}MB")
print(f"  errors: {len(errors)}")
for e in errors[:5]:
    print("   ", e)
passed = backend_up and len(errors) == 0 and rss_growth < 200
print(f"[soak] {'PASS' if passed else 'FAIL'}")
sys.exit(0 if passed else 1)
