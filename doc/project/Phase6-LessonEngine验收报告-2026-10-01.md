# Phase 6 Acceptance Report — Lesson 引擎（教训提炼）

**日期**：2026-10-01 00:35（GMT+8）
**项目**：Open-LLM-VTuber（AI 女友陪伴设备 · Linux AI Brain）
**服务器**：10.1.1.10 · `/home/uu/桌面/Open-LLM-VTuber`
**提交**：`43f4494`（基线 `afd47b3` Phase 5 报告归档）

---

## 1. Overall Result

**PASS**（§七 验收矩阵 7 项全过；LLM LIVE 因外部 API 欠费沿用 BLOCKED 注记，mock 全覆盖）

## 2. Lesson Domain

```
LessonRecord:      PASS（lesson_id/conf_uid/source_reflection_ids/lesson/
                   confidence/metadata/created_at/updated_at；
                   validate() 允许建议性措辞、拒绝硬策略词——
                   必须/务必/一定要/策略/强制/must/always/never/policy/strategy；
                   source_reflection_ids 必填：无来源的断言直接拒绝）
LessonRepository:  PASS（save 前校验；仅依赖 StorageProvider；
                   save/get/list_lessons/list_by_reflection/delete/count）
LessonEngine:      PASS（analyze_reflections 规则层 + analyze_reflections_llm
                   异步精炼；空集 no-op；错误日志+跳过）
LessonAnalyzer:    PASS（RuleAnalyzer min_support=5 门槛——弱观察不产教训；
                   LLMAnalyzer 复用 LTM 流协议 + JSON 校验 + 外来 id 拒绝 +
                   privacy 门控 + 失败回退）
```

**与 Phase 5 的边界差异**（按规范）：Reflection 是纯事实（连"建议"都拒）；Lesson 允许温和建议措辞（"先共情通常更适合继续对话"）——教训本质是可复用建议——但**拒绝命令式/策略性**表述，且**绝不自动注入** Prompt/行为。

## 3. StorageProvider Integration

```
SQLite:  PASS（lessons 表 + idx(conf_uid, created_at)，加法式零迁移）
Hermes:  PASS（sidecar 承载，同 reflections 模式；LIVE 实证
         save/get + list_by_reflection + 跨实例重启恢复）
协议:    29→35 方法（+save/get/list/list_by_reflection/delete/count_lesson）
```

## 4. Persistence

```
Create/Save: PASS（CRUD 往返 + 字段级一致 + advisory 措辞接受）
Get:         PASS（miss -> None）
List:        PASS（list_lessons 最新优先 + list_by_reflection 溯源过滤）
Restart:     PASS（新 provider 实例 count/get 全保持；Hermes LIVE 同验）
Duplicate:   PASS（同 id 重复 save 单行覆盖，updated_at 刷新）
```

## 5. LLM Analysis

```
LLM 调用:   PASS（复用 extractor 流协议 + wait_for 超时 + 失败 warning 回退）
输出校验:   PASS（合法 JSON→LessonRecord.validate()；
            硬策略词/无效 JSON/外来 reflection id/空 lesson/空来源
            —— 5 类拒绝用例全过）
LIVE 注记:  BLOCKED（方舟 API 欠费；mock 全链路已验证）
```

## 6. Isolation

```
conf_uid 隔离: PASS（A/B 互不可见，跨 conf get -> None）
隐私:         PASS（LLM 输入经 privacy_check 门控；复用 privacy.py 零新规则）
```

## 7. Error Handling

```
异常隔离:      PASS（分析失败 logger.warning + 跳过；空集 INFO no-op）
无自动策略:    PASS（零 conversation hook；零 Memory/Prompt/Agent 改动；
              single_conversation.py 无 lesson import——服务器扫描实证）
无 silent fallback: PASS（LLM 失败回退规则层是明示同域降级，非跨后端切换）
```

## 8. Regression Tests（Phase 3/4/5）

```
run_tests.py（Phase 3 Memory）        PASS 41/41
ltm_phase3_contract.py（LIVE）        PASS 104/104
ltm_phase4_tests.py（LIVE）           PASS 34/34
ltm_phase5_tests.py（LIVE）           PASS 39/39
smoke（含扩展后 FakeProvider 协议）   PASS 45/45（本地）
```

## 9. Performance

**OK** —— 规则层纯内存过滤（服务器 LIVE 全套 40 项秒级完成）；LLM 层离线批处理（batch_size 20、超时保护）；零对话路径影响。

## 10. Tests（实际执行）

```
本地:   phase6 36/36 + 回归 41+45+72+32+36 全 PASS
服务器: phase6 40/40（LIVE，含 Hermes sidecar 三项 + conversation
        no-import 扫描）+ 回归 41+104+34+39 全 PASS
MD5:    本地↔服务器 9/9 一致；生产数据目录零测试遗留
```

## 11. Problems Found

| # | 问题 | 影响 | 修复 | 文件 |
|---|---|---|---|---|
| 1 | 测试断言笔误：missing-sources 用例误传了来源导致 4 项连锁失败 | 测试误报 | 去掉来源参数，4 项复测全过 | ltm_phase6_tests.py |
| 2 | LLM LIVE 无法实测 | 见 §5 注记 | mock 全覆盖（欠费为环境问题，Phase 5 起已知） | — |

## 12. Files Changed

```
新增：
  src/open_llm_vtuber/lesson/__init__.py    门面（is_enabled/get_repository/get_engine）
  src/open_llm_vtuber/lesson/schemas.py     LessonRecord + validate()
  src/open_llm_vtuber/lesson/repository.py  LessonRepository
  src/open_llm_vtuber/lesson/engine.py      LessonEngine
  src/open_llm_vtuber/lesson/analyzer.py    RuleAnalyzer + LLMAnalyzer
  tools/ltm_phase6_tests.py                 测试矩阵（§六 8 节）
修改：
  long_term_memory/storage/provider.py      +6 聚合方法（29→35）
  long_term_memory/storage/sqlite_provider.py +lessons 表 + 6 方法 + 行映射
  long_term_memory/storage/hermes_provider.py +sidecar lessons + 6 委托
  long_term_memory/__init__.py              _DEFAULT_CONFIG + lesson 块
  tools/ltm_phase2_smoke.py                 FakeProvider 补 6 方法
删除：无
```

## 13. Database Changes

```
Schema Changed: YES（加法式）
  新表: lessons (lesson_id PK, conf_uid, source_reflection_ids JSON,
        lesson, confidence, metadata JSON, created_at, updated_at)
  新索引: idx_lsn_conf (conf_uid, created_at)
  机制: CREATE TABLE IF NOT EXISTS（老库零迁移）
```

## 14. Migration

```
SQLite→Hermes: NO（Lesson 走 provider 配置双后端，无历史数据迁移）
```

## 15. Dual Write

```
NO
```

## 16. Remaining Limitations

1. **规则层教训是观察复读**（"基于 N 次互动的观察：<observation>"）——真正的语义提炼依赖 LLM 层（LIVE 待 API 恢复）；min_support=5 保证只有强信号才成教训。
2. **无去重/合并**：同一反思反复分析会生成多条同源教训（按 id 各自独立）；未来可加内容哈希去重（记录在册）。
3. **无自动消费方**：教训只存储备查，注入决策属未来 Phase（按规范禁止自动注入）。
4. **LLM LIVE BLOCKED**（方舟欠费）——mock 已验证全链路。

## 17. Final Status

```
Lesson 域完整（模型+仓储+引擎+分析器）    PASS
SQLite 持久化（CRUD+重启）              PASS
conf_uid 隔离                          PASS
LLM 输出验证（含禁词/溯源）             PASS
无自动策略（零注入/零行为改动）          PASS
Storage 隔离（零直连）                  PASS
回归测试（Phase 3/4/5 全绿）            PASS
```

# Phase 6: READY FOR PHASE 7

*按阶段边界纪律：本报告输出后停止，不自动进入 Phase 7。*
