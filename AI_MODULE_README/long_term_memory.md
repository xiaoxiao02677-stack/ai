# 长期记忆模块（`src/open_llm_vtuber/long_term_memory/`）

> 模块化重构 · 模块说明。更新：2026-09-30（Phase 1A MemoryStore 解耦完成后）。

## 职责

LTM = 独立长期记忆子系统：从对话轮抽取记忆 → 去重/合并 → SQLite 持久化 → 检索 →
注入 Agent prompt。**零外部依赖**（不 import 项目内其他模块），可独立成包；
被调用方仅 `conversations/single_conversation.py`。

## 目录结构

```
long_term_memory/
├── __init__.py            # 门面 (369)：get_config/save_config/get_manager/
│                          #   build_retrieval_context/extract_from_turn
├── manager.py             # MemoryManager：抽取-存储-检索编排 (246)
├── extractor.py           # LLM 记忆抽取 (277)
├── retriever.py           # 检索：关键词+向量混合策略 (219)
├── store.py               # MemoryStore 兼容门面 (150)——见下
├── storage/               # Phase 1A 新增：持久化分层（495 行）
│   ├── __init__.py        # 导出 5 类 + 分层说明 (29)
│   ├── sqlite_provider.py # SQLiteStorageProvider：连接/锁/DDL/行映射 (135)
│   ├── memory_repository.py   # MemoryRepository：记忆 CRUD + search_active (155)
│   ├── state_repository.py    # StateRepository：用户状态 get/save (54)
│   ├── summary_repository.py  # SummaryRepository：摘要 + turn_count (56)
│   └── keyword_repository.py  # KeywordRepository：关键词 upsert/list/delete (66)
├── schemas.py             # MemoryRecord / UserState / KeywordRecord dataclass
├── deduplicator.py        # 记忆去重/合并
├── keyword_extractor.py   # 关键词抽取
├── privacy.py             # 隐私过滤
├── prompt_builder.py      # 检索结果 → prompt 片段
└── run_tests.py           # 自测：41 检查（不依赖 pytest）
```

## Phase 1A 解耦：三层结构

```
MemoryStore（store.py，兼容门面，25 方法原签名）
   ├─ MemoryRepository    ──┐
   ├─ StateRepository       │ 全部只依赖 ↓，不互相依赖
   ├─ SummaryRepository     │
   └─ KeywordRepository   ──┘
            ↓ 共享同一实例（单连接单锁）
        SQLiteStorageProvider（sqlite_provider.py）
            ↓
   long_term_memory_data/<conf_uid>.db（4 表，DDL 与原版逐字一致）
```

- **调用方零改动**：manager.py（15 处调用）/ retriever.py（3 处）/ memory_panel.py（17 处）/
  run_tests.py 均未修改，`self.store = MemoryStore(conf_uid)` 照常工作。
- **单连接单锁不变**：4 个 repository 共享同一个 provider 实例（同一 sqlite 连接 +
  同一把 `threading.Lock`），并发语义与原版一致。
- **`stats()` 留在门面**：跨 repository 聚合查询，属门面职责。
- **未来 Hermes provider**：接口契约已在 `storage/__init__.py` docstring 备案
  （需提供 conf_uid / db_path / _lock / _conn / 行映射 等同面）。**本阶段未实现、未引用。**

## 对外兼容契约（改动前逐一核实过的调用方）

- `manager.py` — 15 处：add_memory / update_memory / get_memory / list_memories /
  mark_used / upsert_keyword / list_keywords / get_state / save_state / save_summary /
  get_summary / bump_turn_count / get_turn_count / close
- `retriever.py` — 3 处：list_keywords / search_active / list_memories
- `memory_panel.py` — 17 处：含 list_all_memories / delete_keyword / save_summary /
  get_turn_count / save_state（面板独占使用的 API 也全部保留）
- `run_tests.py` — 直接调用 store 全量方法
- `store.py` 对外属性：`conf_uid` / `db_path`（property 委托 provider）

## 已知死代码（登记于 CLEANUP.md #8，未删）

`find_active_by_content` / `count_memories`（零调用方）+ `__init__.py:30 _DATA_DIR`
（零使用）。为保持 API 兼容原样保留，下一阶段决策。

## 验证方式

- 自测套件：`python -m src.open_llm_vtuber.long_term_memory.run_tests`
  （重构前 41/41，重构后 41/41）
- 双运行等价性：`/tmp/equiv_1a.py`（新旧实现同场景对比 19 项观测值，
  归一化非确定性字段后 19/19 一致）
- 补充验证：`/tmp/verify_1a.py`（28 检查，面板相关 API；25/28 通过，
  3 项失败为脚本自身算术预期错误——原版 `save_summary` 语义即为覆盖 turn_count）
