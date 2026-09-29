# Conversations 模块（`src/open_llm_vtuber/conversations/`）

> 模块化重构 · 模块说明。更新：2026-09-30（模块 #2 chat_group 归位完成后）。

## 职责

**会话编排层**：WebSocketHandler（入口层）与 Agent 层之间的中间层。接收客户端输入
（文本/ASR 转写），组装 BatchInput，驱动 Agent 对话，把结果句推送 TTS 播报，并存储
聊天历史 + 触发长期记忆抽取。

## 目录结构

```
conversations/
├── __init__.py
├── conversation_handler.py   # 入口分发：single/group → 绑定 websocket_send
├── single_conversation.py    # 单人流程：ASR→LTM 检索→Agent→TTS→历史存储（含 LTM 钩子）
├── group_conversation.py     # 多人/多角色轮转
├── chat_group.py             # ChatGroupManager + Group dataclass + 3 个 async 编排器（模块 #2 归位）
├── conversation_utils.py     # 共享流程工具（batch_input 构造、句子/音频输出处理）
├── tts_manager.py            # TTSTaskManager：TTS 任务队列/中断
└── types.py                  # 会话数据类型
```

## chat_group.py（模块 #2，2026-09-30 归位）

原包根级 `src/open_llm_vtuber/chat_group.py`（299 行）→ 本目录，`git mv` 纯搬移，
MD5 全程不变（`385620edb2c80384405d1c5e82c30770`）：

- `Group` dataclass —— 多人会话状态
- `ChatGroupManager` —— create / add / remove / cleanup / get 群组生命周期
- 3 个 async 编排器：`handle_group_operation` / `handle_client_disconnect` / `broadcast_to_group`

**依赖**：纯叶子（仅 stdlib / typing / dataclasses / fastapi / loguru，零项目内部依赖）。
**调用方**（全仓仅 2 处 import，均已更新）：

- `websocket_handler.py:10` — `from .conversations.chat_group import (…)`（4 个公开名）
- `conversation_handler.py:9` — `from .chat_group import ChatGroupManager`

## 依赖方向（实测）

```
websocket_handler.py（唯一上游）
        ↓
conversations/ ──→ agent/agents/ ──→ agent/stateless_llm/（叶子）
        ↓                ↓
long_term_memory/（叶子）   chat_history_manager.py
```

无循环依赖。`long_term_memory/` 与 `chat_history_manager` 为叶子，`chat_group.py` 归位后
同样是叶子——多人会话状态管理不依赖会话流程代码，方向干净。

## 相关记录

- 移动详情见 git commit `99c4705`
- 每轮对话的 LTM 抽取/检索钩子在 `single_conversation.py`（模块 #1 拆分时已核实接线）
