# Phase 4 Acceptance Report — Experience Memory（体验记忆）

**日期**：2026-09-30
**项目**：Open-LLM-VTuber（AI 女友陪伴设备 · Linux AI Brain）
**服务器**：10.1.1.10 · `/home/uu/桌面/Open-LLM-VTuber`
**提交**：`bf4be53`（基线 `6292978` Phase 3.1 + 报告归档）

---

## 1. Status

**READY FOR PHASE 5**（§三十六 30 项验收标准全部满足；见 §10-§17 逐项证据）

## 2. Repository State

- **Branch**：`main`，与 `origin/main` 同步（`0 0`）
- **Commit**：`bf4be53 feat(experience): Phase 4 - Experience Memory (interaction episodes)`
- **关键文件（全部实读，非假设）**：
  - `conversations/single_conversation.py`（228→279 行，+51）
  - `agent/agents/basic_memory_agent.py`（329 行，未改）
  - `chat_history_manager.py`（history_uid 机制：`时间戳_uuid.hex`，JSON 文件）
  - `mcpp/tool_executor.py`（tool_call_status 事件源：`{type, tool_id, tool_name, status, content, timestamp}`）
  - `long_term_memory/`（manager/store/schemas/storage/*，Phase 3.1 终态）
  - `config/memory_panel.py`（仓库根 config/，LTM 消费者，未改）
  - 服务器 git HEAD 与本地 MD5 9/9 一致

## 3. Architecture

```
single_conversation.py（integration layer，3 处最小 hook + 后台任务）
    ↓ 每 turn 一次
ExperienceEngine（engine.py，确定性捕获状态机——零 LLM/零分析）
    ↓ finalized ExperienceRecord
persist_record（__init__.py 门面，asyncio.to_thread 后台执行）
    ↓
ExperienceRepository（repository.py，领域规则：privacy 门控/时间戳/截断）
    ↓ +5 聚合方法
StorageProvider（既有协议，19→24 方法）
    ├── SQLiteStorageProvider（experiences 表）
    └── HermesStorageProvider（sidecar experiences 数组）

MemoryManager / MemoryRetriever / prompt_assembler：零改动（平行域，Experience 未触碰 Memory 子系统）
```

## 4. Experience Schema（最终实际字段）

```python
ExperienceRecord:
    experience_id: str          # uuid16hex
    conf_uid: str               # 人格隔离键
    history_uid: str            # 复用现有会话 ID（不建第二套 session）
    interaction_type: str       # chat | proactive
    user_input: str             # privacy 门控后（敏感→置空+metadata 审计）
    ai_response: str            # privacy 门控后，截断 600
    tool_calls: [{"tool_name","status"}]   # 仅名称+状态，无 payload/secret
    outcome: str                # 事实描述（"AI 已回复，用户可继续对话"）
    outcome_type: str           # turn_complete | ai_error | empty_reply
    metadata: dict              # privacy_dropped 审计等
    started_at / finalized_at / created_at / updated_at: float
```
**不含** user_response/feedback 字段（本轮系统无法可靠获得，不编造——§六 合规）。

## 5. StorageProvider Changes（新增 contract）

```
save_experience(record) / get_experience(id) / list_experiences(limit)
delete_experience(id) / count_experiences()
```
聚合形态、领域对象进出，与既有 19 方法同风格；TYPE_CHECKING 前向引用避免运行时循环依赖；Memory/Keyword/State/Summary 契约零改动。

## 6. SQLite

- **schema**：新表 `experiences`（14 列）+ `idx_exp_conf(conf_uid, finalized_at)`，`CREATE TABLE IF NOT EXISTS` **加法式**——老库零迁移，下次打开自动补表
- **persistence**：INSERT OR REPLACE（同 id 更新语义），与 memories 表同模式
- **restart**：实测 PASS（brand-new provider 实例 count/get/list_by_history 全保持）
- **isolation**：实测 PASS（confA/confB 互不可见，跨 conf get_by_id → None）

## 7. Hermes

**SUPPORTED**（sidecar 路径）：Hermes 无 interaction-episode 概念，experiences 落 provider 本地 sidecar JSON（同 keywords/state/summary 既有模式）。实测：LIVE add/get + **跨 provider 实例 restart persistence PASS**；远端停机时 experience 照常本地持久化（无数据丢失、无跨后端静默回退——memory 操作在同 provider 上仍显式抛 `HermesUnavailableError`）。

## 8. Conversation Integration（single_conversation.py 具体 hook）

1. **import 区**：`experience as _xp`（try/except 双路径，与 LTM 同款）+ `_xp_capture_tasks` 强引用集合
2. **turn 开始**：`ExperienceEngine.start(conf_uid, history_uid, chat|proactive)`
3. **输入后**：`_xp_engine.record_user_input(input_text)`
4. **tool_call_status 分支**：`_xp_engine.record_tool(tool_name, status)`
5. **agent 异常 catch**：`_xp_agent_errored = True`
6. **AI 回复入库后**：`record_ai_response` + `record_outcome` + `finalize(turn_complete|empty_reply|ai_error)` → `asyncio.create_task(asyncio.to_thread(_xp.persist_record, record))` + done_callback
- **净增 51 行**；prompt_assembler/retriever/MemoryManager 零改动

## 9. Capture Lifecycle

```
start(确定性 ID+时间戳) → record_user_input → record_tool×N(事件驱动)
→ record_ai_response → record_outcome → finalize(幂等：二次调用保持首次戳，
  finalize 后拒写) → persist_record(后台线程)
```
全程 deterministic：零 LLM、零 embedding、零额外远端调用。

## 10. Tests（Phase 4，tools/ltm_phase4_tests.py）

| 项 | 结果 |
|---|---|
| 1. Record 创建 + 序列化往返 | **PASS** |
| 2. SQLite CRUD + restart + isolation（12 项） | **PASS** |
| 3. Capture lifecycle（finalize 终态/幂等/新 ID） | **PASS** |
| 4. Privacy（敏感置空+metadata 审计+正常通过） | **PASS** |
| 5. Failure semantics（sidecar 本地可靠/memory 显式失败） | **PASS** |
| 6. Hermes LIVE（add/get + restart persistence） | **PASS** |
| 7. Forbidden-feature 架构扫描 | **PASS** |
| **合计（服务器，LIVE）** | **34/34 PASS** |

**真实服务验证（xp_e2e_lite，7/7 PASS）**：连真实 WebSocket → 发消息 → experience 落生产库 → history_uid 关联 → outcome 语义 → 新 provider 实例可读。**在 LLM 端点 403 欠费窗口跑通**——证明捕获链路确定性、独立于 LLM 健康。

## 11. Phase 3 Regression

| 项 | 结果 |
|---|---|
| run_tests（Memory/Keyword/State/Summary/Turn CRUD） | **PASS 41/41** |
| smoke（1B↔4 行为对拍 + 边界扫描 + FakeProvider 协议含新 5 方法） | **PASS 45/45** |
| contract LIVE（sqlite+hermes-mock+hermes-LIVE 三后端契约） | **PASS 104/104** |
| MemoryManager / Retriever / Provider factory / duplicate save | 全含于上述套件 **PASS** |
| E2E | 14/15（**F3 失败 = 方舟 API 账户欠费 403**，非代码问题——服务器日志实证 `AccountOverdueError`；上一轮 Phase 3.1 同一测试 15/15） |

## 12. Architecture Scan

**PASS**：experience 包 4 文件——零 reflection/lesson/knowledge/strategy/learning（docstring 排除后的真实代码扫描）；零 sqlite3/httpx/requests/HermesProvider 直连；依赖方向 `experience → long_term_memory.storage.provider`（仅抽象）。single_conversation.py 仅 import experience 门面。

## 13. Performance

- **LLM call 增加**：**0**（确定性捕获）
- **同步阻塞**：**0**（persist 经 `asyncio.to_thread` 走线程池；engine 记录是内存操作）
- **chat latency 影响**：无（首版实现曾误用 `create_task(sync_fn)` 被实测抓出 "a coroutine was expected"，已修为 to_thread——见 §18）

## 14. Privacy

- **保存 secret**：**NO**——复用 LTM `privacy_check`（同一套 API key 正则 + 中英关键词表，零新增规则）：敏感字段**整体置空**（不存掩码残片）+ `metadata.privacy_dropped` 审计
- **tool_calls**：仅 `{tool_name, status}`，无 payload/参数/密钥
- **不存**：系统 prompt、内部 agent state、完整 tool 输入

## 15. Files Changed（commit `bf4be53`）

```
新增：
  src/open_llm_vtuber/experience/__init__.py    门面（record_turn/persist_record/get_repository/is_enabled）
  src/open_llm_vtuber/experience/schemas.py     ExperienceRecord
  src/open_llm_vtuber/experience/repository.py  ExperienceRepository（privacy 门控）
  src/open_llm_vtuber/experience/engine.py      ExperienceEngine（状态机）
  tools/ltm_phase4_tests.py                     测试套件
修改：
  src/open_llm_vtuber/conversations/single_conversation.py   +51 行 hook
  src/open_llm_vtuber/long_term_memory/__init__.py           experience.enabled 开关（默认 true）
  src/open_llm_vtuber/long_term_memory/storage/provider.py   +5 协议方法
  src/open_llm_vtuber/long_term_memory/storage/sqlite_provider.py    +experiences 表 +5 方法
  src/open_llm_vtuber/long_term_memory/storage/hermes_provider.py    +sidecar experiences +5 方法
  tools/ltm_phase2_smoke.py                    FakeProvider 补 5 方法（协议扩展连带）
删除：无
```

## 16. Database Changes

```
table:    experiences（新增，加法式 CREATE IF NOT EXISTS）
columns:  experience_id PK / conf_uid / history_uid / interaction_type /
          user_input / ai_response / tool_calls(JSON) / outcome / outcome_type /
          metadata(JSON) / started_at / finalized_at / created_at / updated_at
indexes:  idx_exp_conf (conf_uid, finalized_at)
migration: 无需（老 .db 下次打开自动建表；生产 mao_pro_001.db 已验证；
          测试数据已清零——xp cleaned: 0, mem cleaned: 0）
```

## 17. Forbidden Features Check

```
Reflection = NOT IMPLEMENTED
Lesson = NOT IMPLEMENTED
Knowledge = NOT IMPLEMENTED
Strategy = NOT IMPLEMENTED
Learning = NOT IMPLEMENTED
Self-learning = NOT IMPLEMENTED
Personality evolution = NOT IMPLEMENTED
（静态扫描零命中；prompt_assembler/MemoryRetriever/MemoryManager 零改动；
Experience 不注入 prompt、不进 retriever、不进 MemoryManager）
```

## 18. Known Limitations（真实列出）

1. **user_response/feedback 未捕获**：本轮无法可靠获得用户对 AI 回复的反应（下一轮输入属另一 episode）；字段留待未来反馈通道（如显式点赞/情绪信号）接入，当前不编造。
2. **ai_error 判定条件**：`agent 流异常且无文本产出` 才标 ai_error；LLM 把错误文本作为**正常流内容**返回时（如本次 403 的 "Error calling..." 文本），outcome_type 仍是 turn_complete（捕获的是流内真实文本，无法区分）。日志可交叉核对。
3. **每 turn 一条 experience**：长对话多轮各成一条（by-design：episode = turn 粒度）；跨 turn 的"事件聚合"属未来 Reflection 层职责。
4. **Hermes 后端下 experience 在 sidecar**：与 memory 的远端持久化不同级（本地 JSON）；若需远端集中存储需 Hermes API 增 episode 端点（未来工作）。
5. **`_xp_tool_calls` 列表已废弃未用**（hook 直接调 engine.record_tool）——一行残留，无行为影响，下次清理。
6. **E2E F3 受 API 欠费阻塞**：本阶段 E2E 14/15（环境问题有日志实证）；Experience 专项验证用 xp_e2e_lite 7/7 补全（含真实 WebSocket 全链路）。

## 19. Final Decision

```
Domain（独立 experience/ 包，平行于 Memory）          PASS
Persistence（SQLite 表 + Hermes sidecar 双后端）      PASS
Capture（确定性引擎，零 LLM）                         PASS
Restart（双后端跨实例持久实证）                       PASS
Isolation（conf_uid 隔离 + privacy 门控）            PASS
Regression（41 + 45 + 104 + lite 7 全绿）            PASS
Architecture（静态扫描 + 依赖方向）                  PASS
```

**READY FOR PHASE 5**

---
*按 §三十八 停止条件：本报告输出后停止，不自动进入 Phase 5 Reflection。*
