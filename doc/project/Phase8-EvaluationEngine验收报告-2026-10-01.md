# Phase 8 Acceptance Report — Evaluation Engine（策略评估 / 决策层）

**日期**：2026-10-01 02:10（GMT+8）
**项目**：Open-LLM-VTuber（AI 女友陪伴设备 · Linux AI Brain）
**服务器**：10.1.1.10 · `/home/uu/桌面/Open-LLM-VTuber`

---

## 1. Repository baseline

```
Commit:  378aa8d（基线 b86d6c5 = Phase 7 报告归档）
Branch:  main，origin/main 同步 0 0，工作区干净
协议:    StorageProvider 41→47 聚合方法（+6 evaluation）
生产数据: 未触碰（测试全部在临时 conf_uid，验后零遗留）
```

## 2. Phase 8 architecture

```
StrategyRepository（读候选）──┐
                              EvaluationEngine
当前上下文（纯数据入参）──────┤   ├─ RuleAnalyzer（确定性关键词重叠，先跑）
                              │   └─ LLMAnalyzer（可选精炼，复用 LTM 流协议）
                              ↓
                       EvaluationRepository
                              ↓
                       StorageProvider（SQLite 表 / Hermes sidecar）
```

五层蒸馏链完整：Experience → Reflection → Lesson → Strategy → **Evaluation（适用性判断）**。

## 3. EvaluationRecord

```
evaluation_id · conf_uid · strategy_id（必填溯源——无来源即拒绝）
applicable(bool) · relevance[0-1] · confidence[0-1] · condition_match[0-1]
reason（事实描述，禁命令式措辞）· evidence（命中关键词）
metadata · created_at · updated_at（epoch 浮点，全库一致）
反向追溯：Evaluation → Strategy（strategy_id）→ Lesson（source_lesson_ids）
        → Reflection（source_experience_ids）→ Experience
```

## 4. RuleAnalyzer — PASS

确定性、可解释：CJK 二元组 + 词元归一化重叠。condition_match = 命中/条件关键词数；applicable 需 ≥1 命中且 ≥0.15。relevance = 0.7×match + 0.3×strategy.confidence。reason 明示"命中条件关键词 2/12（用户、焦虑）"。实测强/弱/零匹配/空上下文四档边界全过（弱单点命中 0.083 < 阈值正确判不适用）。

## 5. LLMAnalyzer — PASS（mock；LIVE 见 §22）

完全复用 LTM 流协议（chat_completion + wait_for 超时 + privacy 门控）。**strategy_id 溯源来自引擎而非 LLM**（结构性杜绝外来 id）。拒绝链实测：坏 JSON / 越界分数 / 命令式 reason / 超时错误——全部显式回退规则层（INFO 日志，非静默换算法）。

## 6. StorageProvider — PASS

+6 聚合方法（save/get/list/list_by_strategy/delete/count_evaluation），不凑数。既有 41 方法零改动。

## 7. SQLite — PASS

`evaluations` 表（12 列，加法式 CREATE IF NOT EXISTS）+ 双索引 `idx_evl_conf(conf_uid, created_at)`、`idx_evl_strategy(strategy_id)`（反向溯源路径）。老库零迁移。

## 8. Hermes — PASS（sidecar）

LIVE 实测：save/get + **list_by_strategy 反向溯源** + 跨 provider 实例重启恢复。Hermes 失败语义沿用 Phase 3.1（memory 域显式报错，无跨后端静默回退）。

## 9. Provider Factory — PASS（未改动）

sqlite|hermes 二分支原样；新聚合方法由两个 provider 分别实现；无效 provider 仍 ValueError。

## 10. conf_uid isolation — PASS

A/B 双命名空间：list/get/count/evaluate/reverse-trace 全隔离实测（跨 conf get → None）。

## 11. Provenance — PASS

Evaluation→Strategy 强制（schema validate）；五层链逐级 id 传递；`list_evaluations_by_strategy` SQL 级索引直查。

## 12. Idempotency — **VERIFIED（设计决策）**

同 (strategy_id, context) 重复评估**产生新记录**（各带时间戳）——评估是历史快照而非状态，保留演变轨迹有分析价值；不引入去重机制（规范 §十七允许 + 已记录）。测试显式断言此行为。

## 13. Error isolation — PASS

空策略列表→no-op（INFO）；空上下文→no-op + 明示 reason；策略不存在→warning + skip（**绝不伪造评估**）；analyzer 异常→warning + 跳过；LLM 超时/坏输出→显式规则层回退（无假评估）。

## 14. Conversation boundary — PASS

single_conversation.py 零 evaluation import/use（服务器实测扫描）。

## 15. Memory boundary — PASS

MemoryManager / MemoryRetriever 零改动零引用（实测扫描）。

## 16. Agent boundary — PASS

agent/ 目录零改动；评估不触发任何动作。

## 17. Prompt boundary — PASS

prompt_builder.py 零改动；评估不进任何 prompt。

## 18. Personality boundary — PASS

人格/角色配置零触碰；无行为规则自动生成。

## 19. Tests（实际执行，非静态检查）

```
本地:   phase8 44/44（hermes LIVE BLOCKED）+ 回归 41+45+72+32+36+36+39 全 PASS
服务器: phase8 48/48（LIVE，含 conversation/Memory/Retriever/Prompt 四隔离扫描
        + Hermes sidecar 三项 + 规则阈值边界 + 5 类 LLM 拒绝 + 幂等性断言）
        回归 41 + 104(LIVE) + 34(LIVE) + 39(LIVE) + 40(LIVE) + 43(LIVE) 全 PASS
MD5:    8/8 一致；生产数据目录零测试遗留
```

§二十测试矩阵逐项：Schema×8 / RuleAnalyzer×5 / LLMAnalyzer×7 / Storage×7 / Engine×7——全数覆盖。

## 20. Regression

Phase 3（41+104）/ Phase 4（34）/ Phase 5（39）/ Phase 6（40）/ Phase 7（43）——**全 PASS，零回归**。

## 21. Known limitations

1. 规则层是词法重叠（无语义理解）——"好累"与 condition"疲惫"无字面重叠时不命中；语义匹配属 LLM 层职责
2. 阈值（≥1 命中且 ≥0.15）为经验值，写死可解释；未来可配置化
3. evaluate_candidates 遍历全量候选（per-conf 数量小无碍；万级以上需索引预筛）
4. 无自动消费方：评估结果只存储备查（规范§十二：不接真实 Conversation）

## 22. FAIL / BLOCKED / UNVERIFIED

```
FAIL:        无
BLOCKED:     LLM 真实 API 评估（方舟账户欠费 403，自 Phase 4 起已知环境问题）
             —— mock 已验证全部校验链；充值后可即测，本报告不将 mock 冒充真实 API
UNVERIFIED:  无（LLM LIVE 以外全部项均有实测证据）
```

## 23. Final decision

```
EvaluationRecord        PASS     RuleAnalyzer           PASS
LLMAnalyzer             PASS     Strategy provenance    PASS
conf_uid isolation      PASS     SQLite persistence     PASS
Hermes persistence      PASS     restart                PASS
reverse trace           PASS     error isolation        PASS
idempotency behavior    VERIFIED Phase 3-7 regression  PASS
Conversation isolation  PASS     Memory isolation       PASS
Prompt isolation        PASS     Agent isolation        PASS
Personality isolation   PASS
```

# READY FOR PHASE 9

*按阶段边界纪律：本报告输出后停止，不自动进入 Phase 9。评估到此为止——applicability ≠ execution。*
