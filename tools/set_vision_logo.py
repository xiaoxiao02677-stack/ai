#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
set_vision_logo.py — 摄像头/屏幕图标"无视觉模型变红"开关

逻辑：
  当 conf.yaml 里配置的 LLM 不是视觉(多模态)模型时，把前端侧边栏/底部的
  摄像头图标(FiCamera) 和 屏幕图标(FiMonitor) 染成红色，提示用户：
  "摄像头/屏幕可以打开，但当前 AI 看不到画面"。
  一旦换成视觉模型，重新运行本脚本即可恢复默认颜色。

用法：
  python3 tools/set_vision_logo.py            # 依据 conf.yaml 自动判定
  python3 tools/set_vision_logo.py --on        # 强制红色(无视觉模型)
  python3 tools/set_vision_logo.py --off       # 强制默认色(有视觉模型)
  python3 tools/set_vision_logo.py --check      # 仅打印判定结果，不改文件

判定优先级：
  1) conf.yaml 中存在 `vision_enabled: true|false`   -> 显式覆盖
  2) 否则取当前 llm_provider 对应的 model 名，匹配视觉关键词
     (gpt-4o / pixtral / qwen-vl / glm-4v / claude-3 / gemini / ...)

环境变量 OLLVM_PROJECT 可覆盖项目根目录(默认 /home/uu/桌面/Open-LLM-VTuber)。
"""
import os
import re
import sys
import argparse
from pathlib import Path

PROJECT = Path(os.environ.get("OLLVM_PROJECT", "/home/uu/桌面/Open-LLM-VTuber"))
INDEX = PROJECT / "frontend" / "index.html"
CONF = PROJECT / "conf.yaml"

VISION_KEYWORDS = (
    "vision", "gpt-4o", "gpt-4o-mini", "pixtral", "qwen-vl", "qwen2-vl",
    "qwen2.5-vl", "llava", "glm-4v", "glm-4.5v", "claude-3", "claude-3-5",
    "claude-3-7", "gemini", "internvl", "minicpm-v", "molmo", "yi-vl",
    "deepseek-vl", "smolvlm", "nanonets", "janus", "idefics", "mistral-v",
)

MARK_START = "<!-- VISION_WARN_START -->"
MARK_END = "<!-- VISION_WARN_END -->"

BLOCK = MARK_START + """
<script id="vision-warn">
(function(){
  var RED='#e53e3e';
  function isCam(s){return !!(s.querySelector && s.querySelector('circle[cx="12"][cy="13"][r="4"]'));}
  function isMon(s){return !!(s.querySelector && s.querySelector('rect[width="20"][height="14"]'));}
  function paint(){document.querySelectorAll('svg').forEach(function(s){if(isCam(s)||isMon(s)){s.style.color=RED;}});}
  paint();
  if(window.MutationObserver){try{new MutationObserver(paint).observe(document.documentElement,{childList:true,subtree:true});}catch(e){}}
  window.addEventListener('load',paint);
  [800,2500,5000].forEach(function(t){setTimeout(paint,t);});
})();
</script>
""" + MARK_END


def strip_block(html: str) -> str:
    pat = re.compile(re.escape(MARK_START) + ".*?" + re.escape(MARK_END), re.S)
    return pat.sub("", html)


def _detect_via_yaml() -> "tuple[bool|None, str]":
    try:
        import yaml  # type: ignore
    except Exception:
        return (None, "yaml-unavailable")
    try:
        cfg = yaml.safe_load(CONF.read_text(encoding="utf-8"))
    except Exception as e:
        return (None, f"yaml-parse-error:{e}")
    try:
        agent = cfg["character_config"]["agent_config"]["agent_settings"]["basic_memory_agent"]
    except Exception:
        agent = {}
    if isinstance(agent.get("vision_enabled"), bool):
        return (agent["vision_enabled"], "explicit")
    provider = agent.get("llm_provider")
    if not provider:
        return (None, "no-provider")
    try:
        model = cfg["character_config"]["agent_config"]["llm_configs"][provider]["model"]
    except Exception:
        return (None, "no-model")
    return (_model_is_vision(model), f"model={model}")


def _model_is_vision(model: str) -> bool:
    m = (model or "").lower()
    return any(k in m for k in VISION_KEYWORDS)


def _detect_via_regex() -> "tuple[bool|None, str]":
    try:
        text = CONF.read_text(encoding="utf-8")
    except Exception as e:
        return (None, f"read-error:{e}")
    m = re.search(r"vision_enabled\s*:\s*(true|false)", text, re.I)
    if m:
        return (m.group(1).lower() == "true", "explicit")
    mp = re.search(r"llm_provider\s*:\s*'([^']+)'", text)
    if not mp:
        return (None, "no-provider")
    provider = mp.group(1)
    cfg_start = text.find("llm_configs:")
    if cfg_start < 0:
        return (None, "no-llm-configs")
    seg = text[cfg_start:]
    pb = re.search(r"\n[ \t]*" + re.escape(provider) + r"\s*:\s*\{", seg)
    if not pb:
        return (None, "provider-block-not-found")
    lead = len(pb.group(0)) - len(pb.group(0).lstrip())
    rest = seg[pb.start():]
    mend = re.search(r"\n[ \t]{0," + str(lead) + r"}[A-Za-z_][A-Za-z0-9_]*[ \t]*:", rest[1:])
    block = rest[: mend.start() + 1] if mend else rest
    mm = re.search(r"model\s*:\s*'([^']+)'", block)
    if not mm:
        return (None, "no-model-in-block")
    model = mm.group(1)
    return (_model_is_vision(model), f"model={model}")


def detect_vision() -> "tuple[bool|None, str]":
    res, why = _detect_via_yaml()
    if res is not None:
        return (res, why)
    return _detect_via_regex()


def apply(on: bool) -> None:
    html = INDEX.read_text(encoding="utf-8")
    html = strip_block(html)
    if on:
        if "</head>" in html:
            html = html.replace("</head>", BLOCK + "\n</head>", 1)
        else:
            html = html + BLOCK
    INDEX.write_text(html, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Toggle red camera/screen logo when no vision model.")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--on", action="store_true", help="force red (no vision)")
    grp.add_argument("--off", action="store_true", help="force default color (vision)")
    grp.add_argument("--check", action="store_true", help="only print decision")
    args = ap.parse_args()

    if args.on:
        vision = False
    elif args.off:
        vision = True
    else:
        vision, why = detect_vision()
        if vision is None:
            print(f"[set_vision_logo] 无法判定视觉模型({why})，默认按【无视觉模型】处理(图标变红)。")
            vision = False
        else:
            print(f"[set_vision_logo] 判定: {'有视觉模型' if vision else '无视觉模型'} (依据: {why})")

    if args.check:
        print(f"[set_vision_logo] 当前将渲染: {'红色警告图标' if not vision else '默认图标'}")
        return 0

    apply(on=not vision)
    state = "已注入红色警告脚本" if not vision else "已恢复默认图标(移除红色脚本)"
    print(f"[set_vision_logo] {state} -> {INDEX}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
