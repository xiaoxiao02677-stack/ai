#!/usr/bin/env python3
"""One-shot patch: migrate server files from public-HTTP:12393 to
TLS-on-12393 + internal-127.0.0.1:12395 layout.

1. frontend/index.html  : bootstrap always syncs wsUrl/baseUrl from page origin
                          (fixes stale ws:// cached values from the http era).
2. src/.../live/bilibili_live.py : hardcoded ws://localhost:12393/proxy-ws -> 12395
3. scripts/run_bilibili_live.py  : (no port inside; informational check only)
4. conf.yaml            : system_config.host -> 127.0.0.1, port -> 12395
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
changed = []


def patch(path, old, new, must=True):
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    if old not in text:
        if must:
            print(f"[FAIL] pattern not found in {path}:\n{old[:120]}...")
            sys.exit(1)
        print(f"[skip] {path}: pattern already gone (likely patched)")
        return
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    changed.append(path)


# 1. frontend bootstrap: replace the whole old comment+script block
old_block_start = """    <!-- LAN fix: derive backend WebSocket/Base URL from the current page origin.
         Upstream build hardcodes ws://127.0.0.1:12393, which breaks remote/LAN access. -->
    <script>
      (function () {
        try {
          if (!window.location.host) return;
          var wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
          var wsUrl = wsProto + "//" + window.location.host + "/client-ws";
          var baseUrl = window.location.origin;
          var wanted = { wsUrl: wsUrl, baseUrl: baseUrl };
          Object.keys(wanted).forEach(function (k) {
            var cur = null;
            try {
              cur = window.localStorage.getItem(k);
            } catch (e) {}
            var needs =
              !cur ||
              cur.indexOf("127.0.0.1") >= 0 ||
              cur.indexOf("localhost") >= 0;
            if (needs) {
              window.localStorage.setItem(k, JSON.stringify(wanted[k]));
            }
          });
        } catch (e) {}
      })();
    </script>"""

new_block = """    <!-- LAN + HTTPS fix: ALWAYS derive backend WebSocket/Base URL from the current
         page origin. Overwrites stale cached values (ws:// from the old http era),
         so wss:// is used on https pages and mixed-content never blocks the socket. -->
    <script>
      (function () {
        try {
          if (!window.location.host) return;
          var wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
          var wsUrl = wsProto + "//" + window.location.host + "/client-ws";
          var baseUrl = window.location.origin;
          window.localStorage.setItem("wsUrl", JSON.stringify(wsUrl));
          window.localStorage.setItem("baseUrl", JSON.stringify(baseUrl));
        } catch (e) {}
      })();
    </script>"""

patch("frontend/index.html", old_block_start, new_block)

# 2. bilibili live hardcoded proxy url -> internal port
patch(
    "src/open_llm_vtuber/live/bilibili_live.py",
    'proxy_url = "ws://localhost:12393/proxy-ws"',
    'proxy_url = "ws://localhost:12395/proxy-ws"',
)

# 3. conf.yaml: bind backend to loopback + internal port (ruamel not needed,
#    plain text replace keeps comments; values are simple scalars)
conf = ROOT / "conf.yaml"
text = conf.read_text(encoding="utf-8")
new_text = re.sub(
    r"(?m)^(\s*)host: ['\"]?0\.0\.0\.0['\"]?(\s*#.*)?$",
    r"\1host: '127.0.0.1'\2",
    text,
    count=1,
)
if new_text == text:
    print("[FAIL] conf.yaml host 0.0.0.0 not found")
    sys.exit(1)
new_text2 = re.sub(
    r"(?m)^(\s*)port: 12393(\s*#.*)?$",
    r"\1port: 12395\2",
    new_text,
    count=1,
)
if new_text2 == new_text:
    print("[FAIL] conf.yaml port 12393 not found")
    sys.exit(1)
conf.write_text(new_text2, encoding="utf-8")
changed.append("conf.yaml")

print("[OK] patched:", ", ".join(changed))
