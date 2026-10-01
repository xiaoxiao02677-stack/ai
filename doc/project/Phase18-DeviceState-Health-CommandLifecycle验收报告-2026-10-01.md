# Phase 18 验收报告 — Device State + Health + Command Lifecycle + Observability

**日期**：2026-10-01 20:00（GMT+8）
**项目**：Open-LLM-VTuber · **服务器**：10.1.1.10（VMware VM）
**提交**：`199e11b`（基线 `63ccd67` = Phase 17 报告；代码基线 `ee0e816`）

---

## 1. Baseline

HEAD `199e11b`，origin/main 0 0，工作区 clean。P16/P17 报告 + session/observer 目标对象（DeviceSession/DeviceRegistry/DeviceCommand/DeviceAck/DeviceProtocol/RealTCPTransport/ESP32Adapter/ExecutionResult/固件）实际代码已核。

## 2. DeviceState — PASS

Session 的**只读快照**（View 非权威）：每次读取即时派生（session 字段逐字一致实测；hello/heartbeat/advertisement/disconnect 各事件后快照更新实测）；`last_command_*`/`last_ack_status` 来自命令历史。**无第二状态机**（connection_state ≡ session.state 逐字）。未知设备 → None。

## 3. DeviceHealth — PASS

**全计算零冗余存储**：`heartbeat_age = now - last_seen`（实测无第二时间戳字段）；`protocol_compatible`/`capability_valid` 派生；状态词表仅 ONLINE/STALE/DISCONNECTED（无 GOOD/BAD/HAPPY 类业务态——测试显式断言）。未知设备 → NOT_FOUND。

## 4. DeviceObserver — PASS

只读 API：get_device_state / get_device_health / get_command_status / device_ready + observe_* 事件记录。**零 send/execute/retry/queue/schedule 方法**（扫描）；零 transport/AI/storage/LLM import（扫描）；无法触达 Adapter 或 Transport（不导入即不可达）。

## 5. CommandLifecycle — PASS

七态 CREATED/SENT/ACKED/NACKED/REJECTED/TIMEOUT/FAILED（镜像 P16 适配器语义，非第二套业务枚举）；观察只记录不决策；**transport_accepted ≠ device executed** 保持（SENT 与 ACKED 分离）。

## 6. DeviceReadiness — PASS

`device_ready` = P17 命令门禁的**派生只读读取**（send_allowed 薄封装）；ready≠授权（kill switch 不因 ready 改变——实测）。

## 7. Session integration — PASS

快照与 Session 全字段一致（§2）；五类事件（hello/heartbeat/advertisement/ack/disconnect）全部正确反映。

## 8. ACK integration — PASS

observe_ack 五重完整性：未知 command_id 忽略且**不建历史**；device_id 伪造忽略（记录不动）；异 command_id 的 ack 不能完成待定命令；重复 ack exactly-once 不再完成；合法路径 ACKED/NACKED 正确记录 error_code。

## 9. Timeout / late ACK — PASS

TIMEOUT 终态后迟到 ACK：**结果不重写**（保持 TIMEOUT）+ 仅置 `late_ack` 观测标志。

## 10. Duplicate ACK — PASS

第二次同 command_id ACK 返回 False，ack_at 不变（exactly-once 逻辑完成）。

## 11. Security — PASS

§31 矩阵全测：unknown device/session/state 消息 fail closed；protocol/capability mismatch 拒；invalid ACK/wrong command_id/wrong device_id 忽略；duplicate/late 不改结果。observe_terminal 拒绝非终态输入（ValueError 防误用）。

## 12. Memory bounds — PASS

CommandHistory FIFO 上限 64（MAX+10 写入后 len=64、最旧淘汰实测）；无无限 list/dict。

## 13. Persistence — 零

无新 DB 表（device_state/device_health/command_history/device_events 全无——扫描实证）；全内存运行态。

## 14. Firmware — 零改动（首选路径）

HELLO/HEARTBEAT/ADVERTISEMENT/ACK 已表达全部所需信息——**零新协议消息**（§29 原则执行）。

## 15. Real TCP — 不变

RealTCPTransport 零改动；未新增 MockTCPTransport。

## 16. Test Matrix

```
本地/服务器: 47/47（snapshots×8 / health×8 / lifecycle×11 含 bounded 64 /
            ACK integrity×6 / security+readiness×10 / isolation×6）
```

## 17. Phase 3–17 Regression — 全 PASS

737 项：41+104(LIVE)+34+39+40+43+48+51+51+56+67+49+**59(P15)+33(P16)+49(P17)**。七个核心对象语义零破坏。

## 18. Security Scan — PASS

code-only；RealTCPTransport 的 socket 为合法 transport 层（区分识别）；observer.py 零副作用零旁路。

## 19. Real ESP32 Status

**BLOCKED 保持**（VMware 环境未变）。Firmware simulation = PASS / Real ESP32 = BLOCKED 严格区分，未伪造。

## 20. Findings

| # | 发现 | 处置 |
|---|---|---|
| 1 | 测试误用 `type(H)`（DeviceHello）当 Heartbeat | 显式 import Heartbeat |
| 2 | 后置 helper 定义 NameError（第二次踩） | 挪至使用前；**教训入技能** |

## 21. HIGH / MEDIUM / LOW

```
CRITICAL: 0    HIGH: 0    MEDIUM: 0    LOW: 0
```

## 22. Final Status — §四十九 十六问逐答

1 能定身份✓ 2 能定 session✓ 3 能判三态✓ 4 能读 capability✓ 5 能判 lifecycle✓ 6 ACK 绑 command_id✓ 7 ACK 绑 device_id✓ 8 late ACK 不篡改✓ 9 duplicate 不重复完成✓ 10 observer 全只读✓ 11 State 不绕 Gateway✓ 12 ready 不绕 Policy✓ 13 零新数据库✓ 14 零业务硬件✓ 15 零 MQTT/BLE✓ 16 实机 BLOCKED 如实✓

**Phase 18: PASS** — 只观察，不编排；快照即真相，门禁仍唯一。
