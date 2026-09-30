# Phase 9 Acceptance Report — Decision Engine（决策层）

**日期**：2026-10-01 02:55（GMT+8）
**项目**：Open-LLM-VTuber（AI 女友陪伴设备 · Linux AI Brain）
**服务器**：10.1.1.10 · `/home/uu/桌面/Open-LLM-VTuber`

---

## 1. Repository baseline

```
Commit:  b2fb4f1（基线 fdcac17 = Phase 8 报告归档；代码基线 378aa8d）
Branch:  main，origin/main 同步 0 0，工作区干净
协议:    StorageProvider 47→54 聚合方法（+7 decision）
生产数据: 未触碰（测试全在临时 conf_uid，零遗留）
```

## 2. Phase 8 compatibility

Evaluation 域零改动；Decision 只经 EvaluationRepository 读评估、**从不直接读 Strategy**（§十链路合规：Strategy→Evaluation→Decision）。

## 3. Decision architecture

```
EvaluationRepository（读评估）──→ DecisionEngine
                                   ├─ RuleDecisionAnalyzer（确定性，先跑）
                                   └─ LLMDecisionAnalyzer（可选精炼，白名单校验）
                                         ↓
                                  DecisionRecord → DecisionRepository
                                         ↓
                                  StorageProvider（SQLite / Hermes sidecar）
                                         ↓
                                       STOP（无任何下游）
```

## 4. DecisionRecord

```
decision_id · conf_uid · status（枚举 selected/abstain/rejected——无模糊串）
selected_evaluation_id + selected_strategy_id（selected 必填且互相印证；
  abstain/rejected 必须为空）· confidence[0-1] · reason（禁命令词）
evidence（候选评估 id 列表）· metadata · created_at/updated_at
```

## 5. RuleDecisionAnalyzer — PASS

确定性三级机制：**eligible 门槛**（applicable 且 confidence≥0.5）→ **排序**（relevance → confidence → condition_match）→ **选择**。decision.confidence = 0.5×选中评估.confidence + 0.5×relevance。reason 明示选择依据（"该评估在候选中同时具有较高 relevance(0.39) 与 confidence(0.56)…"）。

## 6. LLMDecisionAnalyzer — PASS（mock；LIVE 见 §24）

复用 LTM 流协议。**白名单**：LLM 只能选传入的 evaluation_id 或 null（合法弃权）；外来 id 拒绝；**strategy_id 永远取自评估记录而非 LLM**（结构性防改写）；坏 JSON/超时/空输出 → 显式规则回退（日志明示）。

## 7. DecisionEngine — PASS

decide_recent / decide_over / decide_recent_llm。空池 → **记录弃权**（弃权是有效决策）；analyzer 异常 → 显式失败（None）不 crash；毒输入（类型损坏评估）隔离实测。

## 8. StorageProvider — PASS

+7 方法（save/get/list/by_strategy/by_evaluation/delete/count），不凑数；既有 47 方法零改动。

## 9. SQLite — PASS

`decisions` 表（11 列，加法式）+ 双索引 `idx_dec_conf(conf_uid, created_at)`、`idx_dec_eval(selected_evaluation_id)`。老库零迁移。

## 10. Hermes — PASS（sidecar）

LIVE 实测：save/get + **by_evaluation 与 by_strategy 双向反向溯源** + 跨 provider 实例重启恢复。失败语义沿 Phase 3.1（显式报错，无跨后端静默回退）。

## 11. conf_uid isolation — PASS

A/B 双命名空间：list/get/count/reverse-trace 全隔离（跨 conf get → None）。

## 12. Provenance — PASS

六层链完整：Decision → Evaluation（selected_evaluation_id）→ Strategy → Lesson → Reflection → Experience。selected 决策强制双 id 且互相印证（validate 实测：缺失任一/不一致均拒绝）。

## 13. Abstain behavior — PASS（核心安全机制）

四类弃权全实测：空评估列表、全不适用（reason 明示"N 条不适用"）、置信度不足（明示"N 条置信度不足"）、完全平局（明示 "ambiguous candidates" + ambiguous_ids 记录）。弃权决策照常持久化（是有效决策记录）。

## 14. Tie handling — PASS

relevance/confidence/match 三键完全相等的双候选 → **abstain**（绝不随机/顺序偷选）；部分平局由下一级键破解（condition_match / confidence 破解用例均实测 selected 正确方）。

## 15. Error handling — PASS

空池→abstain；全 false→abstain；低置信→abstain；冲突→abstain；LLM 坏 JSON/外来 id/超时→显式拒绝+规则回退；analyzer 异常→warning+显式失败。

## 16. Idempotency — VERIFIED（设计决策）

同评估集重复决策产生**新时间戳记录**（决策历史 by design，同 Phase 8 评估）；不引入去重机制；测试显式断言。

## 17-21. 五大隔离 — 全 PASS

```
Conversation:  single_conversation.py 零 decision import（服务器实测）
Memory:        MemoryManager / Retriever 零改动零引用
Prompt:        prompt_builder.py 零改动
Agent:         agent/ / mcp/ 零改动，决策不触发任何动作
Personality:   人格/角色零触碰
```

## 22. Phase 3–8 regression

```
run_tests 41/41 · contract 104/104(LIVE) · phase4 34/34 · phase5 39/39
phase6 40/40 · phase7 43/43 · phase8 48/48（全 LIVE）——零回归
```

## 23. Phase 9 tests

```
本地:   phase9 46/46 + 回归 41+45+72+32+36+36+39+44 全 PASS
服务器: phase9 51/51（LIVE，含五隔离扫描 + Hermes 双向反向溯源 + 重启 +
        平局弃权 + 6 类 LLM 拒绝 + 毒输入隔离）
MD5:    8/8 一致；生产数据零遗留
```

§三十二矩阵逐项：Schema×9 / Rule×9 / LLM×8 / Storage×8 / Engine×7——全数覆盖。

## 24. BLOCKED / UNVERIFIED

```
FAIL:       无
BLOCKED:    LLM 真实 API 决策（方舟账户欠费 403，Phase 4 起已知环境问题）
            —— mock 验证全部校验链；本报告严格区分 mock 与真实 API，不冒充
UNVERIFIED: 无（LLM LIVE 以外全部项有实测证据）
```

## 25. Known limitations

1. 规则层排序是三级键确定性比较——语义冲突（如两条高相关但建议相反的策略）不可检测，仅字面平局弃权；语义冲突消解属未来 Phase
2. MIN_CONFIDENCE=0.5 与 TIE_EPSILON=1e-9 为写死文档化阈值（规范 §十四：不为此加全局配置）
3. decide_recent 取最近 N 条评估混合多情境——生产应按情境分组后 decide_over（接口已提供）
4. 无自动消费方：决策只存储备查（§三十一：Decision → STOP）

## 26. Final decision

```
DecisionRecord PASS · RuleDecisionAnalyzer PASS · Abstain PASS · Tie PASS
LLM validation PASS（LIVE BLOCKED 注记）· Evaluation/Strategy provenance PASS
conf_uid isolation PASS · SQLite/Hermes/restart PASS · error isolation PASS
Phase 3-8 regression 全 PASS · 五隔离全 PASS
```

# READY FOR PHASE 10

*按 §三十九 最终边界：Decision → STOP。系统拥有了可解释、可追溯、可拒绝决策的 Decision Layer，且仍无任何自动行为副作用。不自动进入 Phase 10。*
