# Phase 7 Acceptance Report — Strategy 引擎（教训到策略层）

**日期**：2026-10-01 01:20（GMT+8）
**项目**：Open-LLM-VTuber（AI 女友陪伴设备 · Linux AI Brain）
**服务器**：10.1.1.10 · `/home/uu/桌面/Open-LLM-VTuber`
**提交**：`2a8cc9e`（基线 `2311e0d` Phase 6 报告归档）

---

## 摘要

Phase 7 交付 **Strategy 域**：从 Lesson（教训）提炼可复用的「情境→做法」指南（StrategyRecord），并经既有 StorageProvider 架构持久化。四层蒸馏链至此完整：

```
Experience（发生了什么）→ Reflection（数据显示什么）→ Lesson（可复用教训）
  → Strategy（什么情境下倾向什么做法）
```

策略仅存储备查，**绝不自动注入** Prompt/行为/Memory/Agent。

## 核心变更

```
新增：strategy/ 五件套（schemas/repository/analyzer/engine/门面）
  schemas     StrategyRecord：condition（情境）+ recommendation（建议）
             + source_lesson_ids/evidence（双重溯源）
             validate()：允许建议性措辞；拒绝硬性用语——
             必须/务必/一定要/强制/**策略**/must/always/never/policy/strategy
             （"策略"一词本身即指令性措辞，不入存储文本）；
             无来源拒绝；evidence 必须是 source_lesson_ids 子集
  repository  save 前强制校验；仅依赖 StorageProvider
  analyzer    RuleAnalyzer：按共享来源 reflection 聚簇——
             min_lessons=2（两条相互印证的教训才成策略；孤立教训不产）
             LLMAnalyzer：LTM 流协议 + JSON 校验 + 外来 lesson id 拒绝 +
             privacy 门控 + 失败回退
  engine      analyze_reflections / analyze_reflections_llm（规范命名）；
             空集 no-op；错误日志+跳过；离线批处理专用
修改：StorageProvider 协议 35→41（+save/get/list/list_by_lesson/
      delete/count_strategy）
      SQLite strategies 表（加法式）+ idx(conf_uid, created_at)
      Hermes sidecar（同 lessons 模式）
      smoke FakeProvider 补 6 方法
配置：无新 config 键（规范 §三.7）——运行时默认在 engine/analyzer
```

## 测试矩阵（tools/ltm_phase7_tests.py，§四 8 节）

| 节 | 覆盖 | 结果 |
|---|---|---|
| 1 | CRUD + 序列化 + 建议措辞允许 + 硬性词×3 拒绝（含"策略"）+ 无来源拒绝 + evidence 越界拒绝 + 缺 condition 拒绝 + 重复保存 | **全 PASS** |
| 2 | conf_uid 隔离 + list_by_lesson 溯源（含 miss→空） | **全 PASS** |
| 3 | 重启持久（新 provider 实例） | **PASS** |
| 4 | 规则阈值边界（2 条同源成策略；孤立教训不产） | **全 PASS** |
| 5 | mock LLM 有效产出 + 5 类拒绝（硬性词/坏 JSON/外来 id/缺 condition/空来源） | **全 PASS** |
| 6 | 空输入 no-op + 无 LLM no-op | **全 PASS** |
| 7 | 架构扫描（5 文件零后端直连）+ conversation 零 strategy import | **全 PASS** |
| 8 | Hermes LIVE sidecar（save/get + list_by_lesson + 跨实例重启） | **全 PASS** |

```
本地:   phase7 39/39 + 回归 41+45+72+32+36+36 全 PASS
服务器: phase7 43/43（LIVE）+ 回归 41+104+34+39+40 全 PASS
MD5:    8/8 一致；生产数据零遗留
```

## 边界条件验证

- **无来源断言**：source_lesson_ids 必填——空即 ValueError ✓
- **硬性用语**：中英 10 标记词（含"策略"本身）validate 拒绝 ✓
- **evidence 溯源一致性**：evidence ⊆ source_lesson_ids 强制 ✓
- **阈值语义**：单条孤立教训不成策略（min_lessons=2 相互印证）✓
- **四层链全跑通**：6 Experience → 4 Reflection → 2×4 Lesson → 4 Strategy（条件+建议+溯源完整），重启后反向 by_lesson 检索全通 ✓
- **无自动行为**：conversation/MemoryManager/Retriever/三层 engine 零改动（grep 实证）✓
- **无静默回退**：Hermes 不可用→显式报错（沿 Phase 3.1 语义）✓

## 问题列表

| # | 问题 | 状态 |
|---|---|---|
| 1 | LLM LIVE 策略提炼无法实测（方舟 API 欠费，Phase 5 起已知环境问题） | mock 全覆盖；充值后即测 |
| 2 | 规则层策略的 condition 是观察复读（语义提炼依赖 LLM 层） | 记录在册（与 Phase 6 同型限制） |
| 3 | 无跨策略去重/合并（同簇多次分析产生多条独立策略） | 记录在册，未来 Phase |

## Files Changed

```
新增：strategy/{__init__,schemas,repository,engine,analyzer}.py
     tools/ltm_phase7_tests.py
修改：long_term_memory/storage/{provider,sqlite_provider,hermes_provider}.py
     tools/ltm_phase2_smoke.py
```

## Database Changes

```
Schema Changed: YES（加法式）
  新表: strategies (strategy_id PK, conf_uid, source_lesson_ids JSON,
        condition, recommendation, evidence JSON, confidence,
        metadata JSON, created_at, updated_at)
  新索引: idx_str_conf (conf_uid, created_at)
  机制: CREATE TABLE IF NOT EXISTS（老库零迁移）
```

## Migration / Dual Write

```
SQLite→Hermes: NO    SQLite+Hermes 双写: NO（provider 配置二选一）
```

## Final Status

```
Strategy 域完整（模型+仓储+引擎+分析器）      PASS
SQLite/Hermes 持久化（CRUD+重启+sidecar LIVE） PASS
conf_uid 隔离 + 溯源（lesson 级）             PASS
LLM 输出验证（含禁词/外来 id/子集检查）        PASS
无自动策略（零注入/零行为改动）                PASS
Storage 隔离（零直连）                        PASS
Phase 3/4/5/6 回归全绿                        PASS
```

# Phase 7 READY FOR PHASE 8

*按阶段边界纪律：本报告输出后停止，不自动进入 Phase 8。*
