# Phase 17 验收报告 — Device Session + Heartbeat + Capability Advertisement + Real-Device Readiness

**日期**：2026-10-01 19:20（GMT+8）
**项目**：Open-LLM-VTuber · **服务器**：10.1.1.10（VMware VM）
**提交**：`ee0e816`（基线 `c63f10a` = Phase 16 报告；代码基线 `6d31543`）

---

## 1. Baseline

HEAD `ee0e816`，origin/main 0 0，工作区 clean。P16 报告 + P15 报告 + device_protocol/execution/firmware 实际代码已读。

## 2. Device Identity — PASS

DeviceHello 承载稳定身份：device_id（业务身份；地址样串——IP/MAC/`/`/`@`/`.local`——validate 拒绝）+ device_type + firmware_version + protocol_version。复用 P16 device_id，无第二套 identity。

## 3. DeviceSession — PASS

session_id 每次连接新生成（重连**新 id 永不复用**，旧 session 标 DISCONNECTED）；状态机 CONNECTING→ONLINE→STALE→DISCONNECTED + REJECTED；**单 tick 单转移**（无跳级）；集中时间常量（HEARTBEAT 30s / STALE 90s）；可注入时钟——全部确定性测试无 sleep。

## 4. DeviceRegistry — PASS

内存态 device_id→session；**不持久化、不进 StorageProvider/Hermes/LTM**（session 是 transport 生命周期非 AI 记忆——扫描实证零存储/AI import）；register/lookup/update/remove/disconnect 全备。

## 5. DEVICE_HELLO — PASS

闭 schema（未知字段拒）；验证链：schema → protocol_version（未知拒非 best-effort）→ device_id（空/地址样拒）→ device_type/firmware_version 必填；合法→创建 ONLINE session；非法→REJECTED 不建 session。

## 6. HEARTBEAT — PASS

HEARTBEAT（设备→服务端）只更新 last_seen（+可复活 STALE→ONLINE）；HEARTBEAT_ACK 为 typed 信封；**不携带 AI/conversation/emotion/memory 任何状态**；超时 ONLINE→STALE、再超 STALE→DISCONNECTED；未知设备 heartbeat→None（fail closed）。

## 7. CAPABILITY_ADVERTISEMENT — PASS

设备声明 operation 列表（当前仅 TEST_ECHO）；只记录到 live session；**声明绝不创建/升级服务端 CapabilityContract**（registry 不变实测）；非字符串 operation 拒。

## 8. Command Gate — PASS（P17 核心新增边界）

`send_allowed()`：存在（DEVICE_NOT_FOUND）∩ ONLINE（STALE→DEVICE_STALE / DISCONNECTED→DEVICE_OFFLINE）∩ 协议匹配（DEVICE_PROTOCOL_MISMATCH）∩ operation 已广告（DEVICE_CAPABILITY_UNSUPPORTED）。**Adapter 集成**：门禁拒绝时 Transport.send=0（计数实证）；门禁不取代 ExecutionPolicy/Gateway（两者独立并存实测——kill switch OFF 下正式 Gateway 零命令送达）。

## 9-12. Offline / Stale / Reconnect / 两类 Mismatch — 全 PASS

offline/stale 拒发（机器码区分）；重连新 session_id（≠旧）；协议/能力失配拒发且 send=0。

## 13. Kill Switch — PASS

`GLOBAL_EXECUTION_ENABLED=False` 保持；**设备 ONLINE ≠ 服务端授权**——断路下 Gateway DENY，session gate 不被绕过。

## 14. Security Scan — PASS

session.py 零 storage/hermes/ltm/ai import；零 LLM/agent/MCP；固件扫描：HELLO/HEARTBEAT/ADVERTISEMENT 齐备 + heartbeat_ack 静默消费 + TEST_ECHO only + 零业务硬件 + 零 AI 概念（code-only 剥注释）。

## 15. Firmware — PASS

.ino 增 HELLO+ADVERTISEMENT（连接时）+ HEARTBEAT（10s 周期）+ heartbeat_ack 忽略；无业务 operation；构建/刷写仍 NO（无工具链——如实）。

## 16. Test Matrix

```
本地:   49/49（identity×7 / lifecycle 注入时钟×8 / heartbeat×5 /
        advertisement×4 / gate×7 / adapter 集成×5 / 隔离×3 / 固件×7）
服务器: 49/49 同矩阵
```

## 17. Regression

P3-16 **690 项全绿**（41+104 LIVE+34+39+40+43+48+51+51+56+67+49+57+33；P15 57/57 与 P16 33/33 为 session.py 打包同步后实测）。DeviceCommand/DeviceAck/ExecutionResult/RealTCPTransport 语义零改动。

## 18. Real Device Status

**BLOCKED 保持**（服务器 VMware VM：零 USB 串口/零工具链/零 LAN ESP32）。如实区分：Firmware protocol simulation = PASS；Real ESP32 E2E = BLOCKED。**未伪造。**

## 19. Findings

| # | 发现 | 处置 |
|---|---|---|
| 1 | 测试注入时钟(NOW=1000)与 adapter gate 的真实时钟域冲突 → 会话被判瞬 stale | 集成段改用真实时钟注册；纯 lifecycle 段保持注入时钟（双时钟域各自确定性） |
| 2 | 状态机单 tick 单转移（revive 后需两 tick 才 DISCONNECTED） | 测试按 tick 语义断言；设计本身正确（无跳级） |

## 20. Final Status

**PASS**（§41 全部 STOP 条件未触发：无 policy 绕过/无广告造能力/offline·stale·unknown·mismatch 全拒发/心跳无业务动作/session 不进 Memory/无 LLM/无队列/无重试/无自动发现/无刷写/无真实硬件动作/回归零失败/实机 E2E 未伪造）

*按 §44：STOP——不自动进入 MQTT/BLE/多设备/LED/舵机/电机/音频/TTS/摄像头/麦克风/语音/AI 对话/身体动作系统。等待下一阶段决策。*
