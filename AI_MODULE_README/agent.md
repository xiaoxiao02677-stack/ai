# Agent 模块（`src/open_llm_vtuber/agent/`）

> 模块化重构 · 模块说明。更新：2026-09-30（模块 #1 basic_memory_agent 拆分完成后）。

## 职责

Agent 层 = **业务编排**：接收会话层（conversations/）构造的 BatchInput，驱动 LLM 完成
对话（含工具调用循环），维护短期会话记忆，产出 DisplayText 句子供 TTS 播报。

与基础设施层的边界：

- LLM Provider（`stateless_llm/`）：只管"把消息列表发给模型、拿回流"——**无状态、无业务**
- Agent（本目录）：**有状态**（会话记忆、工具循环状态）、**有业务**（打断处理、prompt 拼装、LTM 上下文注入）
- MCP 工具（`../mcpp/`）：工具注册/执行，被 Agent 调用

## 目录结构

```
agent/
├── agent_factory.py            # 工厂：conf.yaml 的 agent_config.agent_type → 实例化对应 Agent
├── stateless_llm_factory.py    # 工厂：llm_config.provider → 实例化对应 LLM
├── agents/
│   ├── agent_interface.py      # 抽象接口：chat / handle_interrupt / set_memory_from_history
│   ├── basic_memory_agent.py   # 默认 Agent（编排器，330 行）——见下
│   ├── basic_memory_agent_components/   # 拆分出的单一职责组件（v1.2.x 重构新增）
│   │   ├── __init__.py
│   │   ├── session_memory.py         # SessionMemory：短期记忆（历史加载/追加/打断改写）
│   │   ├── prompt_assembler.py       # to_text_prompt / to_messages：prompt 拼装 + LTM 上下文注入
│   │   └── tool_interaction.py       # ToolInteractionLoops：Claude/OpenAI 双工具循环
│   ├── hume_ai.py              # Hume EVI 云端 Agent（独立实现，不走本地 LLM）
│   ├── letta_agent.py          # Letta 云端 Agent（自带 _to_messages，与 basic 无耦合）
│   └── mem0_llm.py             # 空占位文件（见 CLEANUP.md #1）
└── stateless_llm/              # LLM Provider 层（openai_compatible / claude / ollama / llama_cpp / with_template）
```

## basic_memory_agent.py（模块 #1，已完成拆分）

原 720 行多职责文件 → **330 行编排器 + 3 个组件**（总 913 行，全部为原代码原样搬运，
token 级等价验证 12/13 段 EQUAL，1 段为编排器接线 diff，详见重构报告）：

| 组件 | 行数 | 单一职责 | 对外 API |
|---|---|---|---|
| `basic_memory_agent.py` | 330 | 编排：构造依赖、分发 chat/interrupt、组装回调 | `chat` `handle_interrupt` `set_memory_from_history` `start_group_conversation` |
| `session_memory.py` | 134 | 短期记忆数据结构 | `messages` `add_message` `set_from_history` `handle_interrupt` `append` |
| `prompt_assembler.py` | 101 | prompt 拼装（模块级函数） | `to_text_prompt` `to_messages` |
| `tool_interaction.py` | 332 | 双格式工具循环 | `ToolInteractionLoops.claude_loop` `.openai_loop` |

**对外兼容契约**（改动前逐一核实过的调用方）：

- `conversations/single_conversation.py:118` — `await agent_engine.chat(batch_input)`
- `conversations/conversation_handler.py:125,189` — `agent_engine.handle_interrupt(...)`
- `websocket_handler.py:415,440` — `agent_engine.set_memory_from_history(conf_uid, history_uid)`
- `conversations/group_conversation.py:176` — `hasattr(agent_engine, "start_group_conversation")` 守卫
- `conversations/single_conversation.py:198` — `getattr(agent_engine, "_llm", None)`（LTM 抽取复用 Agent 的 LLM；`_llm` 属性名必须保留）
- `agent_factory.py:39-86` — 构造函数签名（13 个参数）原样保留

**内部结构**：

- `self._session_memory: SessionMemory` — 组件持有记忆数据；`self._memory` property 返回
  `self._session_memory.messages`（活引用，兼容旧读写习惯）
- `self._tool_loops: ToolInteractionLoops` — 组件持有 `prompt_mode_flag`（工具循环状态）；
  once-per-turn 打断守卫（`self._interrupt_handled`）与 `interrupt_role` 计算留在编排器
- `to_messages(self._session_memory, input_data)` — prompt 拼装为纯函数，LTM 上下文注入点在
  `input_data.metadata["ltm_context"]`

## 如何新增一种 Agent

1. 在 `agents/` 下新建 `xxx_agent.py`，实现 `AgentInterface` 三方法
2. 在 `agent_factory.py` 注册 agent_type → 类映射
3. 云端一体型 Agent（自带记忆，如 Letta/Hume）可不依赖组件包，独立实现

## 验证方式

- 单元：`src/open_llm_vtuber/long_term_memory/run_tests.py`（41 检查，含 Agent 相关契约）
- E2E：`/tmp/ltm_e2e.py`（15 检查，真实 WS + LLM + LTM 全链路；v2 已修复 init 等待）
- 冒烟：`python -c "from src.open_llm_vtuber.agent.agents.basic_memory_agent import BasicMemoryAgent; from src.open_llm_vtuber.agent.agents.basic_memory_agent_components import SessionMemory, ToolInteractionLoops"`
