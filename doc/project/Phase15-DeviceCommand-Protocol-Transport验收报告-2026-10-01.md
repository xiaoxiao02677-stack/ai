# Phase 15 — Device Command + Protocol + Transport Mock + ESP32Adapter 验收报告

**日期**：2026-10-01 16:55（GMT+8）
**项目**：Open-LLM-VTuber · **服务器**：10.1.1.10
**提交**：`e54b3bc`（基线 `f892ef6` = Phase 14 报告）

---

## 1. 基线

```
HEAD: e54b3bc，origin/main 0 0，工作区 clean
新增范围: device_protocol/（4 文件）+ execution/adapter.py（ESP32Adapter）
         + phase15 测试 + phase13 扫描正则精确化
零改动: gateway/policy/schemas/sandbox/capability/八上游域/Provider
```

## 2-4. DeviceCommand Schema / command_id / 闭 schema — PASS

独立协议层对象（≠ActionIntent）；字段：command_id/device_id/capability/operation/parameters/protocol_version/provenance(四 id)。**闭 schema**：from_dict 拒未知键（additionalProperties=false 语义）；参数纯 string + 长度上限；provenance 键集封闭。**operation 受控枚举**：仅 TEST_ECHO（TEST/MOCK ONLY——零业务动作擅自创造）。**device_id 业务身份**：地址样字符串（IP/`/`/`@`/`.local`）validate 拒绝。

**command_id 确定性派生**：SHA-1(provenance+operation+device) 截 16 hex——同上下文同 id、异上下文异 id（实测）；非随机 UUID；幂等竞态设计就位（重复 send 被 Transport 拒绝并显式 DEVICE_FAILED 上浮）。

## 5-6. DeviceProtocol / Version — PASS

encode（validate→JSON envelope）/ decode（JSON→全量验证→DeviceCommand）双向验证：schema、protocol_version=1（未知拒，无升降级）、operation 枚举、参数、provenance、command_id。确定性、纯函数、零网络。篡改/坏 JSON/错版本/缺字段全拒（实测）。

## 7-8. Transport Interface / MockTransport — PASS

最小接口 `send(message_id, message, timeout)` / `receive(message_id, timeout)`。MockTransport：内存、确定性、零网络零设备零线程零时钟——timeout 语义 = 无 staged 响应。实测：send 成功、重复 id 拒、未 staged → timeout、未知 id → timeout、staged/default 响应。**不实现 retry**。

## 9-10. ESP32Adapter / Adapter Registry — PASS

骨架链：ExecutionRequest → 验证命令 → encode → transport.send → mock 响应 → decode → 结果载荷（**transport_accepted / device_ack 分离**——发送成功 ≠ 设备执行）。声明 **side_effect_level="DEVICE"**——默认 Gateway/Policy 下**永远 DENY**（kill switch OFF 实测），只有授权组合可驱动。无真实网络客户端（仅 Transport 接口）。FakeExecutionAdapter 原样保留（PURE 共存实测）。注册表纪律沿 P13（静态冻结）；ESP32Adapter 未注册进默认表（Phase 16 决策），按需组合。

## 11-12. ACK/Result 边界 / Device Identity — PASS

四态最小语义：TRANSPORT_ACCEPTED / DEVICE_ACK / DEVICE_RESULT /（失败/超时：DEVICE_FAILED / DEVICE_TIMEOUT）。ExecutionResult 复用（P11/13，零重定义）。DeviceIdentity=业务 device_id（地址拒入），不做注册/发现/fleet。

## 13. Security Boundary — PASS

八连拒（unknown command/operation/capability/device/invalid parameter/schema/version/provenance）实测；LLM→ESP32 无路径（device_protocol 零 LLM code-only 扫描）；ActionIntent 无法绕 Gateway（P13 链未动）；Capability 无法绕 Policy（DEVICE 级默认 DENY 实测）。

## 14. End-to-End Mock 链 — PASS

```
ExecutionRequest → ESP32Adapter → DeviceCommand(验证) → Protocol.encode
→ MockTransport.send → staged echo → Protocol.decode → DEVICE_RESULT 载荷
```
确定性（同请求同载荷同 command_id）、可审计（provenance 四 id + transport_accepted/device_ack）、零网络零硬件零副作用。

## 15. Tests

```
本地:   phase15 51/51 + Phase 3-13 全量回归
服务器: phase15 51/51 + Phase 3-13 回归 583 项全 PASS
        （41+104(LIVE)+34+39+40+43+48+51+51+56+67+49）
MD5:    5/5 一致；生产数据零遗留
```

## 16. Phase 3–14 Regression — 全 PASS（见上；Phase 14 为纯文档无需回归）

## 17. Security Scan — PASS

device_protocol/ 四文件 + execution/adapter.py：socket/requests/urllib/http/mqtt/serial/bluetooth/ble/subprocess/os.system/eval/exec/GPIO/ESP-IDF **零真实 I/O 命中**（code-only；"esp32." 仅为 adapter_id 命名——p13 扫描正则已精确化为 espidf/esp-idf）。

## 18. Findings

| # | 发现 | 处置 |
|---|---|---|
| 1 | p13 扫描把 `esp32.` adapter_id 命名误报为设备访问 | 扫描正则精确化（espidf/esp-idf），测试语义不变 |
| 2 | 测试 staging 用错 message_id 导致超时而非 decode 失败分支 | 修正为真实 command_id（分支覆盖恢复） |

## 19. HIGH / MEDIUM / LOW

```
HIGH: 0
MEDIUM: 0
LOW: 1 — MockTransport 的 default_response 是全局的（测试便利）；
       未来真实 Transport 不得携带此概念（设备必须显式应答）。记录在册。
```

## 20. 是否 READY FOR PHASE 16

**READY FOR PHASE 16**（§37 PASS 标准逐项满足：独立协议对象 ✓ 分离 ✓ 闭 schema ✓ 受控枚举 ✓ command_id 确定性 ✓ version 存在 ✓ codec 确定性 ✓ Transport 仅抽象 ✓ Mock 无网络 ✓ Adapter 建立 ✓ 仅经 Gateway+Policy ✓ Fake 保留 ✓ ExecutionResult 复用 ✓ Mock E2E 全通 ✓ 回归全绿 ✓ 真实网络/设备/副作用 = 0 ✓）

*按 §38：STOP——不选型 MQTT/BLE/Serial，不写固件，不连真实设备。Phase 16 单独决策 Transport 实现 + ESP32 Firmware 最小协议。*
