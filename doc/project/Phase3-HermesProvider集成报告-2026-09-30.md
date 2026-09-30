# Phase 3：Hermes Memory Provider 集成 —— 交付报告

**日期**：2026-09-30
**项目**：Open-LLM-VTuber LTM 长期记忆子系统
**服务器**：10.1.1.10 · `/home/uu/桌面/Open-LLM-VTuber`
**提交**：`b4816db`（基线 `6199bed` Phase 2 FINAL）
**结论**：✅ 完成。Hermes Provider 作为第二存储后端接入，SQLite 默认行为零变化，上游全层零改动。

---

## Hermes Version / API

**实际使用的 Hermes**：本项目自建的 **ai-companion「Hermes + Memory Manager 联合记忆系统」**（V1，服务器 `/root/桌面/ai-companion/`）

- **版本**：V1（24 模块 / 2549 行，FastAPI + PostgreSQL 16 + pgvector + fastembed bge-small-zh-v1.5）
- **接口方式**：HTTP REST（`http://127.0.0.1:12396`）
  - `/api/agents/{agent_id}/memories`：GET list / POST create / GET·PATCH·DELETE by id / POST search / GET stats
  - `/api/health` 存活探针
  - `X-User-Id` 头 + `agent_id` 路径双键命名空间隔离
- **官方文档**：`ai_companion/HERMES_MEMORY_REPORT.md` + 源码精读（routes/manager/repository/models/lifecycle/schemas 逐文件核实）

**调查关键澄清（§3"不要猜"的执行记录）**：`hermes_study/` 里的 Nous hermes-agent 是**方向相反**的插件体系——其 `MemoryProvider` ABC 是"外部记忆系统接入 Hermes agent"的接口，内建记忆只是 MEMORY.md/USER.md 两个有字符上限的文件，均非可用后端。真正可调用的 Hermes 记忆服务 = ai_companion REST API，全部语义以源码为准，零猜测。

## Architecture

```
Agent (basic_memory_agent)
  ↓ metadata["ltm_context"]
MemoryManager（manager.py — 零 SQL / 零后端名）
  ↓ MemoryStore（store.py 兼容门面，经 provider_factory(config) 拿后端）
Domain Repositories（storage/repository.py — 领域规则，零 SQL / 零后端名）
  ↓ 19 个聚合方法（MemoryRecord/KeywordRecord/UserState 进出）
StorageProvider（storage/provider.py — Protocol，聚合形态，实现无关）
  ├── SQLiteStorageProvider（sqlite_provider.py — 默认后端，唯一 SQL 所在地）
  └── HermesStorageProvider（hermes_provider.py — REST 适配器）
        ├── Hermes Memory REST API（ai-companion @12396 → PostgreSQL+pgvector）
        └── JSON sidecar（keywords/state/summary/turn_count/use_stats — LTM 域特有聚合）
```

## Files Changed（commit `b4816db`，8 files，+1179/−32）

```
修改：
- src/.../long_term_memory/__init__.py        _DEFAULT_CONFIG 增 storage 块（默认 sqlite）
- src/.../long_term_memory/manager.py         store 构建传入 config（后端选择生效）
- src/.../long_term_memory/store.py           provider 经 create_storage_provider(conf_uid, config)
- src/.../long_term_memory/storage/__init__.py 导出面 + 组合点说明
- tools/ltm_phase2_smoke.py                   打包清单加 Phase3 文件；§5/§9 适配新工厂签名
新增：
- src/.../long_term_memory/storage/hermes_provider.py    HermesProvider 适配器（550 行）
- src/.../long_term_memory/storage/provider_factory.py   config 驱动工厂（70 行）
- tools/ltm_phase3_contract.py                           契约测试（496 行）
```

## Hermes Integration（关键适配决策）

| 差异点 | 适配方式 |
|---|---|
| ID 体系（LTM 16hex vs Hermes uuid） | `metadata.ltm.memory_id` 双向映射 + JSON sidecar 持久；重启后 list 全量重建映射 |
| `deprecated` vs Hermes 生命周期 | deprecated↔superseded（合法转移）；expired/archived 读作 deprecated；deleted 读作不存在；非法转移降级为字段更新+显式 warning |
| keywords/state/summary/turn_count | Hermes 无此概念 → per-conf_uid JSON sidecar（provider 内部细节，协议面不变） |
| `mark_used` | Hermes access_count 只统计检索命中 → sidecar 记 use_count/last_used_at，评分语义不变 |
| 记忆类型 8 vs 6 | Hermes 最接近桶 + `metadata.ltm.ltm_type` 精确保留原值往返 |
| 命名空间 | conf_uid→agent_id（复用现有体系，未造第二套 ID） |

## Provider Contract

`StorageProvider` 协议**零改动**（19 聚合方法签名不变）。同一套契约测试跑三个后端全部通过：create/get/update/delete/find/count/mark_used/metadata 往返/命名空间隔离/状态映射/keywords/state/summary/turns/检索全链路。

## Tests

```
契约套件（31 项 × 3 后端）      SQLite PASS · Hermes(mock) PASS · Hermes(LIVE) PASS
不可用处理（§14）              503→HermesUnavailableError 显式抛出；未知 provider 快速失败
静态依赖（§23）                业务层零 hermes 引用；仅 hermes_provider 懒加载 hermes
run_tests（41 项）             PASS（默认 sqlite 零回归，本地+服务器）
smoke（45 项，1B↔3 对拍等价）   PASS（本地+服务器）
E2E 真实 LLM（15 项）          PASS（sqlite 默认路径，注入/提取/去重/冲突/召回全部不变）
服务器契约总计                 103/103 PASS（LIVE 含真实 REST + PostgreSQL）
```

无隐藏失败。过程问题（LIVE 命名空间撞旧数据、静态段相对路径、契约脚本 env 支持、工厂 monkeypatch 时序）均为测试侧问题，修复后复跑全绿。

## Data Migration

**None** —— 未迁移任何 SQLite 历史数据（生产 `mao_pro_001.db` 未动）。LIVE 测试数据在唯一命名空间下，生产数据目录零污染。

## Dual Write

**Disabled** —— 无任何双写路径；`storage.provider` 二选一，SQLite 默认行为与 Phase 2 逐字节一致（smoke §6 对拍证明）。

## Known Limitations

1. Hermes 停机时 hermes 后端不可用——显式错误进 `[LTM]` 日志，聊天链路照常降级；无自动回退 SQLite（防"写错地方的错觉"）。
2. `find_active_by_content`/`count_memories` 是客户端过滤——per-人格记忆量小无性能问题。
3. sidecar 与 Hermes 非同事务——极端情况可少记一次 use_stats；ID 映射有 metadata.ltm 兜底可重建。
4. expires_at/working TTL 是 Hermes 域概念，LTM 未使用，映射时忽略。
5. ai_companion 冷启动需 fastembed 模型缓存（已用代理预热）。

## Phase 3 Status

**COMPLETE** —— §31 全部 21 项验收标准满足。commit `b4816db` 已推送 GitHub。
