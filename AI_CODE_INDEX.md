# AI_CODE_INDEX.md — AI 辅助导航索引

> 给 AI（和新人）的代码地图：**改 X 应该去哪个文件**。生成：2026-09-30，随重构推进持续更新。
> 行数为重构前扫描值；标注 ♻️ 的行数已按拆分后更新。

## 快速定位表

| 我要改… | 去这里 | 说明 |
|---|---|---|
| 系统人设 / 对话提示词 | `agent/agents/basic_memory_agent_components/prompt_assembler.py` | `to_text_prompt` / `to_messages`；LTM 上下文注入点也在 `to_messages` |
| 聊天主流程（一次问答） | `conversations/single_conversation.py` | ASR→LTM 检索→Agent→TTS→历史存储；LTM 钩子都在这 |
| 工具调用循环（Function Calling） | `agent/agents/basic_memory_agent_components/tool_interaction.py` | `claude_loop` / `openai_loop` 双格式 |
| 短期记忆 / 历史记录格式 | `agent/agents/basic_memory_agent_components/session_memory.py` | `add_message` / `set_from_history` / `handle_interrupt` |
| 打断（用户抢话）处理 | `agent/agents/basic_memory_agent.py`（编排器）+ `session_memory.py` | once-per-turn 守卫在编排器；记忆改写在 SessionMemory |
| 切换/新增 LLM 供应商 | `agent/stateless_llm/` + `agent/stateless_llm_factory.py` | Provider 实现 + 注册 |
| 切换/新增 Agent 类型 | `agent/agents/` + `agent/agent_factory.py` | 实现 AgentInterface + 注册 |
| ASR 识别 | `asr/`（接口 `asr_interface.py`） | sherpa-onnx 等 |
| TTS 合成 / 播报队列 | `tts/` + `conversations/tts_manager.py` | 引擎 + 任务队列/中断 |
| Live2D 模型/表情 | `live2d_model.py` | model_info 加载 |
| WebSocket 会话协议（消息类型） | `websocket_handler.py` | 连接/断开/各消息类型分发 |
| 长期记忆（存取/冲突/召回） | `long_term_memory/` | 独立子系统，仅被 single_conversation 调用 |
| MCP 工具注册与执行 | `mcpp/` | server_registry / tool_manager / tool_executor |
| 配置加载 / 校验 | `config_manager/` | conf.yaml → Pydantic 模型 |
| HTTP 路由 / 静态页 | `routes.py` + `server.py` | 含 /config /memory 面板挂载 |
| 服务组装（DI） | `service_context.py` | 全部引擎单例装配 |

## 文件 ↔ 职责清单（核心区）

### 入口层
- `run_server.py` → `server.py`（207）→ `routes.py`（254）→ `websocket_handler.py`（612）

### 会话层 `conversations/`（7 文件 / 1,378 行）
- `conversation_handler.py`（213）single/group 分发
- `single_conversation.py`（228）单人主流程 + LTM 检索/抽取钩子（:79 / :198-214）
- `group_conversation.py`（394）多人轮转
- `conversation_utils.py`（293）batch_input 构造、句子/音频处理
- `tts_manager.py`（182）TTS 队列与打断
- `types.py`（68）BatchInput / BatchOutput 等

### Agent 层 `agent/`（模块 #1 已重构 ♻️）
- `agents/basic_memory_agent.py`（330 ♻️）编排器：chat 分发 / 打断守卫 / 回调工厂
- `agents/basic_memory_agent_components/session_memory.py`（134 ♻️）短期记忆
- `agents/basic_memory_agent_components/prompt_assembler.py`（101 ♻️）prompt 拼装
- `agents/basic_memory_agent_components/tool_interaction.py`（332 ♻️）工具循环
- `agents/agent_interface.py`（54）抽象基类
- `agents/hume_ai.py`（256）/ `letta_agent.py`（128）云端 Agent
- `agents/mem0_llm.py`（0）空占位（CLEANUP.md #1）
- `agent_factory.py` / `stateless_llm_factory.py` 工厂
- `stateless_llm/`（6 文件 / 891 行）LLM Provider 层

### 记忆层 `long_term_memory/`（11 文件 / ~2,300 行，零内部依赖）
- `__init__.py`（369）门面：get_manager / build_retrieval_context / extract_from_turn / 抽取 LLM 独立配置
- `manager.py`（246）/ `extractor.py`（277）/ `retriever.py` / `store.py`（406）/ `deduplicator.py` / `keyword_extractor.py` / `privacy.py` / `prompt_builder.py` / `schemas.py` / `run_tests.py`（41 检查）

### 基础设施层
- `asr/`（11 文件）、`tts/`、`translate/`、`vad/`、`mcpp/`、`config_manager/`、`data_model/`

## 跨层调用规则（现状事实，非规范）

- `websocket_handler` 是 conversations 的**唯一**调用方
- `single_conversation` 是 long_term_memory 的**唯一**调用方
- Agent 不直接 import conversations / websocket（反向依赖禁止，现状满足）
- `long_term_memory` 不 import 项目内任何其他模块（可独立成包）
- LTM 抽取通过 `getattr(agent_engine, "_llm")` 借用 Agent 的 LLM（`_llm` 属性名是契约）

## 已知债务

见 `CLEANUP.md`（空文件 / 缺失方法 / 冗余分支 / MCP 每连接重建 / None.db 幽灵会话）。
