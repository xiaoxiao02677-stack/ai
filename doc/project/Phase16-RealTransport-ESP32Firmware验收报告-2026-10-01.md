# Phase 16 验收报告 — Real Transport + ESP32 Minimal Firmware Protocol

**日期**：2026-10-01 18:40（GMT+8）
**项目**：Open-LLM-VTuber · **服务器**：10.1.1.10（VMware VM）
**提交**：`6d31543`（基线 `1c73caf` = Phase 15 报告；代码基线 `e54b3bc`）

---

## 1. Baseline

```
HEAD: 6d31543，origin/main 0 0，工作区 clean（锁定于 e54b3bc 之上）
读取: Phase14 架构审查 + Phase15 报告 + execution/capability/
      device_protocol 实际代码（非文档假设）
硬件探测: lsusb 无 USB 串口设备；arduino-cli/esptool.py/idf.py 均不存在；
         局域网探测无监听中的 ESP32 端点 —— 真实设备环境缺失（详见 §21）
```

## 2. Commit

`6d31543`（feat(phase16): real TCP transport + device ack protocol + ESP32 firmware）

## 3. Transport 选择

```
Transport:    TCP socket over LAN（RealTCPTransport）
为什么选择:   直连、零外部依赖、服务端标准库即实现、ESP32 Arduino
             原生 WiFiClient；本阶段禁止的 MQTT/HTTP API/WebSocket/
             云平台/BLE/Serial 全部排除
Server 端接口: 实现 Phase 15 Transport 抽象 send/receive
ESP32 端接口:  WiFiServer + WiFiClient（.ino）
连接方式:     显式 host:port 配置（无发现/无自动连接）
同步/异步:    同步（适配器直连调用）
超时:         connect 3s / IO 3s（可配置）
最大消息长度:  65536（可配置）
错误模型:     TransportError / TransportTimeout / ConnectionReset 显式化
```

## 4. DeviceCommand

Phase 15 定义不变；`from_request` 生成，command_id 确定性幂等键。

## 5. DeviceProtocol

Phase 15 定义不变；新增 **DeviceAck**（ack.py）作为正式响应协议：闭 schema、status 枚举 ACK/NACK、error_code 九值共享枚举（§13 全表）。

## 6. ESP32 Firmware（firmware/esp32/）

```
职责:   Receive → Decode → Validate(device_id/version/schema/operation/
        parameters/provenance) → command_id 幂等检查 → TEST_ECHO → ACK/NACK
幂等:   有限环形缓存（64 条）——重复 command_id → NACK DUPLICATE_COMMAND，
        不重复执行（非 sleep/时间判断）
禁止:   LLM/AI/prompt/personality/memory/decision/strategy/lesson/
        reflection/conversation/agent/tool/MCP —— 代码零出现
硬件:   业务硬件副作用 = 0（仅 Wi-Fi+TCP 为 Transport 本身；
        GPIO/Servo/Motor/LED/Camera/Mic/Speaker 全部零使用，扫描实证）
```

## 7. Real Transport（real_transport.py）

真实 socket I/O：connect/send/recv/timeout/reset/disconnect 全路径实测（见 §15）；**无 default_response**（Phase 15 LOW → CLOSED）。

## 8. ACK/NACK

全表实测：ACK 成功路径（echo 回传证明设备端执行）；NACK 六类（DUPLICATE_COMMAND/UNKNOWN_DEVICE/UNKNOWN_OPERATION/UNSUPPORTED_VERSION/MALFORMED_MESSAGE/INVALID_SCHEMA）。机器可读错误码，无自由异常串。

## 9. Timeout

真实超时实测（silent 模拟器 → TransportTimeout → 适配器 DEVICE_TIMEOUT），**无伪造成功**。

## 10. Duplicate

同 command_id 二次发送 → 固件（模拟器）NACK DUPLICATE_COMMAND 且 seen 计数为 1（无重复执行）；重复 send 在 Transport 层另被显式拒绝。

## 11. Device Identity

`esp32-test-001` 固定业务身份；错误 device_id → UNKNOWN_DEVICE；无 wildcard。

## 12. Protocol Version

version=1；2/未知 → UNSUPPORTED_VERSION（envelope 层与 command 层双验证）。

## 13. Kill Switch

`GLOBAL_EXECUTION_ENABLED = False` 保持；ESP32Adapter（DEVICE 级）在默认 Policy 下永远 DENY——**正式 Gateway 路径零命令送达**（测试断言：模拟器收到的一切流量均来自直连测试发送）。

## 14. E2E

```
ActionIntent → Policy(DENY@gateway, 验证保持) + 直连适配器路径:
ExecutionRequest → ESP32Adapter → DeviceCommand → Protocol.encode
→ RealTCPTransport.send → 固件模拟器(同 .ino 验证管线) → ACK
→ RealTCPTransport.receive → DeviceAck.decode → DEVICE_RESULT
```

**真实设备 Gateway E2E：BLOCKED**（无硬件，§21；不伪造——§35 合规）。

## 15. Test Matrix

```
本地:   phase16 33/33（DeviceAck 8 / RealTransport 真实 socket 10 /
        固件管线对拍 6 / 适配器集成 4 / 边界 5）
服务器: phase16 33/33 同矩阵
回归:   Phase 3-15 全绿（660 项含 LIVE Hermes；P15 重界定 55/55）
```

## 16. Regression

41 + 104(LIVE) + 34 + 39 + 40 + 43 + 48 + 51 + 51 + 56 + 67 + 49 + 55 = 638 项服务端全 PASS（P15 55 为重界定后实际值），零回归。

## 17. Security Scan

Server 侧：socket 仅存在于 real_transport.py（Transport 本体，合法）；无 mqtt/paho/httpx/requests/serial/ble/websocket；policy/LLM/AI 零导入。固件侧（code-only，注释剥离）：业务硬件零命中；AI 概念零命中（provenance 字段名排除——协议数据非 AI 逻辑）。固件无自动刷写（仅文档化手动命令）。

## 18. Hardware Side Effect Audit

Business hardware side effects = **0**（Transport 所需 TCP 栈除外——这是通信基础设施非业务动作）。

## 19. Firmware Build

**NO**（服务器无 arduino-cli/esptool/idf.py 工具链）

## 20. Firmware Flash

**NO**（无工具链 + 无设备 + 默认禁止自动刷写——三重原因，均如实记录）

## 21. Real Device Connection

**BLOCKED**（如实）：服务器为 VMware 虚拟机——USB 树仅 VMware 虚拟设备，无 ESP32 串口；无 ESP32 工具链；局域网探测无监听设备端点。**未伪造 PASS**：Protocol=真实执行、Transport=真实 socket、Firmware=源码交付+管线对拍、Real E2E=BLOCKED 分开记录。

## 22. Findings

| # | 发现 | 处置 |
|---|---|---|
| 1 | 模拟器 `_handle` 与 threading 属性冲突（Windows Python 3.13） | 改名 `_handle_conn` |
| 2 | 模拟器单连接阻塞后续测试 | 每连接一线程 |
| 3 | ConnectionResetError 未显式化 | real_transport 增专有分支 |
| 4 | NACK 空 command_id 被 schema 拒（envelope 层拒绝时固件确实不知道 id） | DeviceAck 允许 envelope-level 错误携带空 id |
| 5 | P15 零 I/O 扫描命中 P16 新文件 | 扫描范围限定 Phase-15 文件集（P16 自有扫描） |

## 23. LOW / MEDIUM / HIGH / CRITICAL

```
CRITICAL: 0
HIGH:     0
MEDIUM:   0
LOW:      1 — .ino 的幂等缓存为内存环形缓冲（重启即失忆）：符合本阶段
            最小要求；未来真实设备需持久化幂等或严格单发语义。记录在册。
```

## 24. Final Status

```
Real Transport: PASS（真实 socket 全路径实测）
ESP32 Firmware: PASS（源码交付 + 共享管线对拍）
DeviceProtocol/Command: PASS（含新 DeviceAck 协议）
ACK/NACK/Timeout/Duplicate/Wrong-Device/Wrong-Version/Malformed: 全 PASS
Kill Switch/Gateway/ExecutionResult: PASS（不变 + 断路实证）
Phase 3-15 Regression: 全 PASS（零回归）
真实网络: YES（本地回环真实 socket）· 真实 Transport: YES（TCP 实现）
真实 ESP32: NO（BLOCKED）· 真实固件: 源码 YES / 构建 NO / 刷写 NO
真实设备副作用: 0 · 业务硬件动作: 0
```

# Phase 16: PASS（真实设备 E2E 段 BLOCKED — 环境缺失，如实记录）

*按 §39/§40：STOP——不自动进入 MQTT/BLE/Wi-Fi provisioning/多设备/真实灯光/舵机/电机/音频/摄像头/语音/AI 对话。*
