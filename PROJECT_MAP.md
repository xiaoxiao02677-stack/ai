# Project Map — Open-LLM-VTuber

> 生成于 2026-09-30 模块化重构 Phase 2。基于真实扫描（非目录名猜测）。
> 服务器：/home/uu/桌面/Open-LLM-VTuber（git 仓库，基线 commit 992309c）
> 代码量：118 个 .py / 18,257 行（src/ 下，不含 __pycache__）

## 项目入口

| 入口 | 文件 | 说明 |
|---|---|---|
| 主入口 | `run_server.py`（仓库根） | CLI 参数 → 启动 server.py |
| ASGI 应用 | `src/open_llm_vtuber/server.py` (207行) | WebSocketServer；挂载 routes + 静态 |
| HTTP 路由工厂 | `src/open_llm_vtuber/routes.py` (254行) | init_client_ws_route / init_webtool_routes / init_proxy_route |
| WS 主循环 | `src/open_llm_vtuber/websocket_handler.py` (612行) | WebSocketHandler：客户端 WS 会话全流程 |
| DI 容器 | `src/open_llm_vtuber/service_context.py` (572行) | ServiceContext：全服务单例装配（LLM/ASR/TTS/VAD/翻译/MCP） |

## 核心模块

### Conversation（会话编排）
`src/open_llm_vtuber/conversations/` — 7 文件 / 1,378 行
- `conversation_handler.py` (213) — 入口分发：single/group → 绑定 websocket_send
- `single_conversation.py` (228) — 单人流程：ASR→LTM 检索→Agent→TTS→历史存储（**含 LTM 钩子**）
- `group_conversation.py` (394) — 多人/多角色轮转
- `conversation_utils.py` (293) — 共享流程工具（batch_input 构造、句子/音频输出处理）
- `tts_manager.py` (182) — TTSTaskManager：TTS 任务队列/中断
- `types.py` (68) — 会话数据类型
- 被调用方：websocket_handler（唯一调用者）

### Agent（业务 Agent 层）
`src/open_llm_vtuber/agent/` — 19 文件
- `agents/agent_interface.py` (54) — AgentInterface 抽象
- `agents/basic_memory_agent.py` (330 ♻️ 2026-09-30 已拆分) — 编排器：chat 分发、打断守卫、回调工厂（原 720 行多职责文件）
- `agents/basic_memory_agent_components/` (3 文件 ♻️ 新增) — 拆分组件：
  - `session_memory.py` (134) — SessionMemory：短期记忆（历史加载/追加/打断改写）
  - `prompt_assembler.py` (101) — to_text_prompt / to_messages：prompt 拼装 + LTM 注入
  - `tool_interaction.py` (332) — ToolInteractionLoops：Claude/OpenAI 双工具循环
- `agents/hume_ai.py` (256) — Hume EVI 云端 Agent
- `agents/letta_agent.py` (128) — Letta 云端 Agent
- `agents/mem0_llm.py` (0 行，空文件) — 占位（见 CLEANUP.md）
- `stateless_llm/` — 6 文件 / 891 行：LLM Provider 层（stateless_llm_interface 64 / openai_compatible 237 / claude 246 / ollama 73 / llama_cpp 76 / with_template 195）

### LLM
即 `agent/stateless_llm/`（见上）。**业务与基础设施分离良好**：Agent → StatelessLLMInterface → 各 Provider。

### Memory（长期记忆子系统）
`src/open_llm_vtuber/long_term_memory/` — 12 文件 + storage/ 子包（Phase 1A 解耦后）
- 门面：`__init__.py` (369) — get_config/save_config/get_manager/build_retrieval_context/extract_from_turn
- `manager.py` (246) / `extractor.py` (277) / `retriever.py` / `store.py`（薄门面，见下）/ `deduplicator.py` / `keyword_extractor.py` / `privacy.py` / `prompt_builder.py` / `schemas.py` / `run_tests.py`
- `storage/` 子包（Phase 1A 新增，6 文件）：`sqlite_provider.py`（StorageProvider 实现：连接/锁/DDL/行映射）→ `memory_repository.py` / `state_repository.py` / `summary_repository.py` / `keyword_repository.py`（4 个领域仓库，共享同一 provider）→ `store.py` 兼容门面（MemoryStore 保持 25 方法原签名，委托到仓库）
- **零外部依赖**（不 import 项目内其他模块）→ 可独立成包；storage 层为未来 Hermes provider 预留了接口契约
- 被调用方：仅 `conversations/single_conversation.py`

### ASR
`src/open_llm_vtuber/asr/` — 11 文件。接口 `asr_interface.py`；实现：sense_voice (sherpa-onnx) 等。

### TTS
`src/open_llm_vtuber/tts/` — 22 文件。接口 `tts_interface.py`；实现：edge_tts / azure / bark / melo / cosyvoice 等。

### VAD
`src/open_llm_vtuber/vad/` — 4 文件。接口 `vad_interface.py`；实现 silero。

### Translate
`src/open_llm_vtuber/translate/` — 5 文件。接口 + 各云厂商。

### Tools / MCP
`src/open_llm_vtuber/mcpp/` — 8 文件 / ~1,400 行。`server_registry.py` + `tool_manager.py` + `tool_executor.py` (382) + `tool_adapter.py` (232) 等。

### Live / 直播
`src/open_llm_vtuber/live/` — 2 文件。`bilibili_live.py` (351)（含 proxy-ws 对接）。

### Live2D / Avatar
`src/open_llm_vtuber/live2d_model.py` (194，根级) — Live2dModel（模型表情/动作状态）。

### Config
`src/open_llm_vtuber/config_manager/` — 13 文件 / ~2,700 行。`utils.py`（Config 核心加载）+ 按域拆分：asr (375) / tts (816) / stateless_llm (285) 等。

### UI
`frontend/`（Vue 构建产物 + 源码在仓库根）+ `web_tool/`（ASR/TTS 测试页）+ `config/`（配置面板，Python 后端 + 静态前端，**未跟踪**）。管理入口 `https://10.1.1.10:12393/config/`。

### Database
- LTM：SQLite per conf_uid（`long_term_memory_data/<conf_uid>.db`）
- 聊天历史：JSON 文件 per conf_uid（`chat_history_manager.py` → `chat_history/`）
- ai-companion（姊妹项目）：PostgreSQL+pgvector，**不在本项目范围**。

### Utils
`src/open_llm_vtuber/utils/` — 5 文件 / 1,030 行：sentence_divider (608) / tts_preprocessor (196) / stream_audio (86) / install_utils (140)。

## 根级散文件（重构候选）

| 文件 | 行数 | 职责 | 建议 |
|---|---|---|---|
| websocket_handler.py | 612 | WS 会话协议 | 保留位置（入口层） |
| service_context.py | 572 | DI 容器 | 保留 |
| chat_history_manager.py | 374 | 聊天历史 JSON 持久化 | 保留 |
| ~~proxy_handler.py~~ | ~~311~~ | ~~Bilibili proxy WS~~ | ✅ 2026-09-30 已归位 `proxy/proxy_handler.py`（模块 #3，纯搬移，MD5 不变） |
| routes.py | 254 | HTTP 路由 | 保留 |
| server.py | 207 | ASGI 入口 | 9 文件 |
| live2d_model.py | 194 | Live2D 模型 | 保留 |
| ~~proxy_message_queue.py~~ | ~~164~~ | ~~proxy 消息队列~~ | ✅ 2026-09-30 已归位 `proxy/proxy_message_queue.py`（模块 #3，纯搬移，MD5 不变） |
| message_handler.py | 92 | 消息类型分发 | 保留 |
| ~~chat_group.py~~ | ~~299~~ | ~~多人会话状态~~ | ✅ 2026-09-30 已归位 `conversations/chat_group.py`（模块 #2，纯搬移，MD5 不变） |

## 依赖方向（实测）

```
run_server.py → server.py → routes.py → websocket_handler.py
                                              ↓
              conversations/ → agent/agents/ → agent/stateless_llm/（叶子）
                     ↓                ↓
              long_term_memory/（叶子，零内部依赖）   chat_history_manager
```

无循环依赖（import 层面）。`long_term_memory/` 与 `chat_history_manager` 为叶子模块。
