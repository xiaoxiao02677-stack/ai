# Phase 3.1：Hermes Provider 验收与稳定性修补 —— 验收报告

**日期**：2026-09-30
**项目**：Open-LLM-VTuber LTM 长期记忆子系统
**服务器**：10.1.1.10 · `/home/uu/桌面/Open-LLM-VTuber`
**提交**：`b55f062`（基线 `b4816db` Phase 3）
**结论**：✅ PASS WITH ISSUES→修复后全绿。发现 3 个真实缺陷 + 1 个测试竞态，全部最小修复。

---

## 1. Overall Result

**PASS**（4 个问题全部最小修复后，全量测试矩阵绿）

## 2. Provider Contract

```
SQLite:  PASS
Hermes:  PASS（LIVE，真实 REST + PostgreSQL）
```

当前 `provider.py` 真实能力矩阵（19 聚合方法）：

```
Memory   save / get / list(过滤) / list_all / delete / find_active_by_content / count / mark_used
Keyword  upsert / list / delete / count
State    get / save
Summary  get / save / get_turn_count / bump_turn_count
生命周期  close
```

## 3. CRUD

```
Memory:   PASS（双后端，全字段级：memory_id/content/type/importance/confidence/status/created_at/updated_at）
Keyword:  PASS（增删改查 + 不破坏 Memory 实证）
State:    PASS（跨实例重启保持）
Summary:  PASS（跨实例重启保持）
Turn:     PASS（increment→restart→count 不丢）
```

## 4. Persistence

```
Restart:     PASS —— SQLite 与 Hermes 双后端：memory/keyword/state/summary/turn/use_count 全部跨实例持久
ID Mapping:  PASS —— 最高优先级测试实证：Instance A 创建→销毁→Instance B 用原 memory_id
              取回成功；UUID 映射不漂移、不重建记录、无重复（sidecar + metadata.ltm 双保险）
Sidecar:     PASS —— keywords(hit_count=2) / state / summary / turn_count / use_stats 全项重启验证
```

## 5. Failure Handling

```
Hermes unavailable:   PASS —— 死端点运行期 save/get 抛 HermesUnavailableError；
                              新增构造期探活（verify_on_start 默认开），后端停机→启动即显式失败
No silent fallback:   PASS —— 全链路无回退 SQLite 路径；未知 provider 快速失败
```

## 6. Factory

```
SQLite selection:      PASS
Hermes selection:      PASS
Invalid provider:      PASS（ValueError，含错误值回显）
Dead endpoint:         PASS（构造期 HermesUnavailableError）
```

## 7. Architecture

```
Repository isolation:  PASS（零 hermes/httpx/requests//api/agents）
Manager isolation:     PASS（零 hermes/httpx/SQL/SQLite）
Retriever isolation:   PASS
Hermes boundary:       PASS（仅 hermes_provider.py 实现适配器；工厂仅懒加载引用）
排序权:                保留在项目侧 —— Hermes 只做底层召回，retriever 评分逻辑两后端同码运行（LIVE 实测火锅 top-1）
```

## 8. Tests（实际执行）

```
本地:   acceptance 32/32 · contract 72/72 · smoke 45/45
服务器: acceptance 60/60(LIVE) · contract 104/104(LIVE) · run_tests 41/41
真实配置切换: provider=hermes → E2E 15/15（数据真实落 PostgreSQL）
             → 清 28 条测试数据 → 还原 sqlite → E2E 15/15
MD5:    3/3 本地==服务器；生产 mao_pro_001.db 未动
```

执行命令（服务器）：

```
LTM_ACC_SRC=src/open_llm_vtuber/long_term_memory LTM_ACC_HERMES_LIVE=1 \
  uv run python tools/ltm_phase31_acceptance.py
LTM_CONTRACT_SRC=src/open_llm_vtuber/long_term_memory LTM_HERMES_LIVE=1 \
  uv run python tools/ltm_phase3_contract.py
uv run python -m src.open_llm_vtuber.long_term_memory.run_tests
uv run python tools/ltm_e2e.py   # provider=hermes 与 provider=sqlite 各一轮
```

## 9. Problems Found

| # | 问题 | 原因 | 影响 | 修复 | 文件 |
|---|---|---|---|---|---|
| 1 | hermes 配置 + 后端停机 → 静默"无记忆"运行（§12 违规） | 构造不验证连接，运行期异常被 manager 吞 | 数据语义欺骗风险 | verify=True 构造探活 /api/health + verify_on_start 配置 | hermes_provider.py / provider_factory.py |
| 2 | recency 评分偏差 8 小时 | `_parse_ts` 对 naive ISO 按本地时区解释（服务器 +8） | 检索排序失真 | naive 时间戳显式按 UTC | hermes_provider.py |
| 3 | 删除后的记录在映射重建时复活 tombstone | `_ensure_id_map` 不过滤 deleted | 幽灵映射堆积 | 跳过 hidden 状态 | hermes_provider.py |
| 4 | E2E F1/F2 在 hermes 下挂 2 项 | 固定 sleep(6) 对远端后端每条 HTTP 往返不够（非 provider bug——服务器日志证实 relationship 已写入，纯测试时序） | 测试误报 | 30s 轮询断言 | tools/ltm_e2e.py |

## 10. Files Changed（commit `b55f062`，6 files，+499/−12）

```
src/.../storage/hermes_provider.py     修 #1 #2 #3
src/.../storage/provider_factory.py    修 #1（verify_on_start）
src/.../long_term_memory/__init__.py   配置键注释
tools/ltm_e2e.py                       修 #4
tools/ltm_phase3_contract.py           适配 + fail-fast 新断言
tools/ltm_phase31_acceptance.py        新增（验收套件）
```

## 11. Database Changes

```
Schema changed: NO（SQLite DDL 零变化；Hermes PostgreSQL 仅测试数据且已清空）
```

## 12. Migration

```
SQLite → Hermes: NO
```

## 13. Dual Write

```
NO
```

## 14. Remaining Limitations

1. Hermes deleted 是终态——同 memory_id 删除后重存会新建 Hermes 记录（LTM 业务流永远用新 id，无实际影响）。
2. sidecar 与 Hermes 非同事务（极端崩溃可少记一次 use_stats；ID 映射有 metadata.ltm 兜底可重建，已实证）。
3. `find_active_by_content`/`count_memories` 是客户端过滤（per-人格小数据量无碍）。
4. ai_companion 冷启动依赖 fastembed 模型缓存（已预热；换机需重做）。

## 15. Final Status

**Phase 3: READY FOR PHASE 4**

依据 §22 全标准满足：SQLite regression PASS + Hermes contract PASS + ID persistence PASS + restart PASS + failure handling PASS + factory PASS + repository/manager/retriever isolation PASS——并完成真实配置切换双向 E2E（sqlite→hermes→sqlite 各 15/15）。

commit `b55f062` 已推送 GitHub（origin/main 同步 0/0）。
