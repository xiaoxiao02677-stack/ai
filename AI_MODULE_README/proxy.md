# Proxy 模块（`src/open_llm_vtuber/proxy/`）

> 模块化重构 · 模块说明。更新：2026-09-30（模块 #3 归位完成后）。

## 职责

Bilibili/多客户端**代理 WS 通道**：让多个客户端（web 前端 + 直播平台弹幕机）通过
**一条**到真实服务器的 WebSocket 连接复用同一个 VTuber 会话，同时提供会话状态感知的
消息排队，避免直播平台弹幕洪峰直接打到对话管线。

## 目录结构

```
proxy/
├── __init__.py
├── proxy_handler.py         # ProxyHandler：维持单条 server WS 连接 + 多客户端复用
└── proxy_message_queue.py   # ProxyMessageQueue：生产者-消费者消息队列（会话状态感知）
```

（两文件原位于包根级 `src/open_llm_vtuber/`，模块 #3 `git mv` 纯搬移归位，MD5 不变。）

## proxy_handler.py（311 行）

- **类**：`ProxyHandler`
- **入口路径**：`routes.py` `init_proxy_route()` → `/proxy-ws` 端点（需 `system_config.enable_proxy`）
- **核心逻辑**：
  - `connect_to_server()`：用 aiohttp ClientSession 连真实服务器（默认 `ws://localhost:12393/client-ws`）
  - 客户端注册/注销（`clients: Dict[str, WebSocket]`），心跳保活
  - 服务器消息按客户端分发；客户端消息经 `ProxyMessageQueue` 排队后转发给服务器
- **默认连接目标**：`ws://localhost:12393/client-ws`（TLS 代理端口，非 12395 直连——proxy 走公网入口）

## proxy_message_queue.py（164 行）

- **类**：`ProxyMessageQueue`
- **模式**：生产者-消费者（`collections.deque` + `asyncio.Lock` + 消费协程任务）
- **会话状态感知**：`_conversation_active` 置位时消息入队暂存，会话空闲后按序消费转发（`forward_func` 回调）
- **被调用方**：仅 `proxy/proxy_handler.py`（同目录相对导入，全仓唯一）

## 依赖关系

- **零项目内部依赖**（叶子模块）：仅 stdlib / fastapi / aiohttp / starlette / loguru
- **上游调用方**：`routes.py:12`（`from .proxy.proxy_handler import ProxyHandler`，全仓唯一 import 点）
- **下游**：真实服务器的 `/client-ws` 端点（运行时 WS 连接，非 import 依赖）

## 相关记录

- 移动详情见 git commit `0d065c4`（rename 100% 相似度，纯搬移验证）
- 根级尚存备份残件 `server.py.bak-panel`（见 CLEANUP.md #7，未擅动）
