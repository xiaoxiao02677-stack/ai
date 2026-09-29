"""Idle-WebSocket survival probe.

Connects to a WS endpoint, receives nothing special, and just waits.
Prints a timeline of anything received and when/how the connection dies.

Usage:
    python ws_idle_probe.py <url> [--verify]
    python ws_idle_probe.py ws://127.0.0.1:12395/client-ws
    python ws_idle_probe.py wss://127.0.0.1:12393/client-ws
"""

import asyncio
import ssl
import sys
import time

import websockets

URL = sys.argv[1]
VERIFY = "--verify" in sys.argv
WATCH = 75.0  # how long to stay connected if nothing kills us


async def main():
    kwargs = {}
    if URL.startswith("wss"):
        ctx = ssl.create_default_context()
        if not VERIFY:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl"] = ctx
    t0 = time.time()
    print(f"[{0.0:6.1f}s] connecting {URL} verify={VERIFY}", flush=True)
    async with websockets.connect(URL, open_timeout=10, **kwargs) as ws:
        print(f"[{time.time()-t0:6.1f}s] OPEN", flush=True)
        try:
            while True:
                remaining = WATCH - (time.time() - t0)
                if remaining <= 0:
                    print(f"[{time.time()-t0:6.1f}s] still alive after {WATCH:.0f}s, done", flush=True)
                    break
                msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                text = msg.decode(errors="replace") if isinstance(msg, bytes) else str(msg)
                print(f"[{time.time()-t0:6.1f}s] recv: {text[:100]}", flush=True)
        except asyncio.TimeoutError:
            print(f"[{time.time()-t0:6.1f}s] local watch timeout", flush=True)
        except Exception as e:
            print(f"[{time.time()-t0:6.1f}s] DIED: {type(e).__name__}: {e}", flush=True)


asyncio.run(main())
