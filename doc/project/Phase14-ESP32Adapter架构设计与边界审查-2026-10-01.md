# Phase 14 — ESP32 Adapter 架构设计与边界审查

**日期**：2026-10-01 16:35（GMT+8）
**项目**：Open-LLM-VTuber · **服务器**：10.1.1.10
**阶段性质**：ARCHITECTURE REVIEW（纯设计审查，代码修改 = NO，真实通信 = 0）

---

## 1. 当前 Phase 13 架构审查（基于实际代码，非假设）

已逐文件核验（HEAD `4537b81`）：

```
execution/policy.py     ExecutionPolicy.decide(contract, adapter)：
                        PURE→SANDBOX；非 PURE 且 GLOBAL_EXECUTION_ENABLED=False→DENY；
                        未知等级/缺失→DENY。断路器为模块级常量（L31）。
execution/adapter.py    ExecutionAdapter 接口（run(ExecutionRequest)→dict）；
                        FakeExecutionAdapter.side_effect_level 为**类级**属性（L75，
                        Phase 13 修复：子类可声明 DEVICE 并被 policy 识别）；
                        AdapterRegistry 静态冻结。
execution/gateway.py    ExecutionGateway 七步链：溯源→解析→适配器查找→闭 schema→
                        policy.decide→ExecutionRequest 冻结快照→adapter.run→持久化。
capability/             静态注册表 + 确定性 resolver + 闭 schema 契约。
```

**结论（§三问题 1）**：Phase 13 的 Adapter 接口**可以承载**未来 ESP32 Adapter——它已具备全部所需接缝：静态注册（注册即插）、Policy 授权（非 PURE 必须过断路器）、冻结请求入参（intent 不经手）、结果持久化（metadata 全审计）。唯一需要新增的是 adapter 自身的 run 实现与 DeviceCommand 层，**执行链语义零改动**。

## 2. ESP32 Adapter 定位

```
ExecutionGateway
  → policy 授权（side_effect_level=DEVICE → 需断路器 ON + 显式授权源，未来阶段）
  → ESP32Adapter（注册于 AdapterRegistry）
  → DeviceCommand（转换产物）
  → Device Protocol（编码/验证）
  → Transport Boundary（未实现——本阶段只定义接口）
```

**职责（问题 3）**：把已获准的 ExecutionRequest 转换为合法的 DeviceCommand；声明 `side_effect_level="DEVICE"`；返回结果交给 Gateway 组装 ExecutionResult。

**不负责（问题 4）**：重新决策/评估/选能力；修改 ActionIntent；绕过 Policy（policy.decide 在 Gateway 内对 adapter 无感知——adapter 模块不 import policy，Phase 13 已扫描实证）；直接连接 ESP32（Transport 是独立边界）。

## 3. ActionIntent → DeviceCommand 边界

四层对象严格分离（问题 2）：

| 对象 | 层 | 职责 | 生命周期 |
|---|---|---|---|
| ActionIntent | AI 决策层 | 意图（RESPOND/REMIND/ACKNOWLEDGE + 参数） | 不可变持久记录 |
| CapabilityContract | 能力描述层 | 闭 schema 契约 | 静态冻结 |
| ExecutionRequest | 执行边界层 | 冻结快照（已授权） | 单次执行内 |
| **DeviceCommand** | 设备协议层 | 最小、类型安全、可验证的设备命令 | 单次传输内 |
| （TransportPacket） | 传输层 | 帧/编码/寻址 | 未来阶段 |

ActionIntent ≠ DeviceCommand（问题六）：AI 层对象永不直接下设备；转换**只发生在 ESP32Adapter.run() 内部**。

## 4. DeviceCommand schema 原则

```
device_id:    string（AI 身份，见 §5）
command_id:   string（幂等键，见 §10）
capability:   string（capability_id）
operation:    enum（受控操作集，与契约对齐——非自由文本）
parameters:   closed schema（additionalProperties=false；
              键白名单+类型+长度，复用 CapabilityContract.input_schema
              作为唯一参数权威——Phase 11/12 纪律延续）
timestamp:    float（epoch）
provenance:   {action_id, decision_id, evaluation_id, strategy_id}（只读溯源）
```

**禁止**：自然语言设备命令（"让她抬手"）、自由 command 串、shell/function/URL/任意 JSON（问题七/八）。Operation 集未来仅由 Device Protocol 定义扩展，不得随 ActionIntent 扩权。

## 5. Device Identity（问题十）

- **AI Server Device Identity**（业务身份）：`device_id`——服务器注册表中稳定逻辑名（如 `device.body`）；与厂商/地址解耦。
- **Transport Address**：IP/hostname/MAC——纯传输层概念，**永不**作为 AI 层业务身份；映射表未来由 Transport 层维护。
- 本阶段不实现设备注册系统；边界是：Adapter 只认 `device_id`，地址解析是 Transport 职责。

## 6. Device Protocol

位于 `device_protocol/`（未来新域，不在 execution/ 内混入传输细节）。职责：DeviceCommand ↔ 传输帧的**确定性编解码 + 双向验证**（schema 校验、version 校验、字段有界性）。纯数据层——零网络零决策，可独立单测。

## 7. Transport Boundary（问题十一）

本阶段只定义最小接口（不选型、不实现）：

```
send(command_bytes, message_id, timeout) -> send_outcome
receive(message_id, timeout) -> payload | timeout
```

结论写入：**发送成功 ≠ 设备执行成功**（§9 四态边界）。选型（MQTT/HTTP/BLE/Serial）留给 Phase 15+，任何实现必须实现上述接口并受同一 Gateway/Policy 管辖。

## 8. ESP32 Firmware Boundary（问题七/八/十二）

**固件知道**：Device Protocol 编解码、设备身份校验、command schema 验证、允许的操作集、ACK/result 生成。

**固件绝对不知道**：LLM、Reflection/Lesson/Strategy/Evaluation/Decision/ActionIntent/CapabilityResolver/ExecutionPolicy 概念。固件只接收**已裁决的合法 DeviceCommand**，绝不解释 AI 意图。服务端降级/拒止逻辑永不下放固件。

## 9. ACK / Result Boundary

四态必须可区分（本阶段只定义，不实现状态机）：

```
transport accepted   —— 传输层收到字节
device accepted      —— 固件验证通过（schema+身份+操作允许）
device executed      —— 设备能力完成
device failed        —— 任一环节失败（含 timeout）
```

## 10. Idempotency（问题十四）

**结论：DeviceCommand 必须携带 `command_id`（幂等键）**。理由：server timeout + device 已执行 + server 重试的经典竞态，没有幂等键固件无法去重（重复执行设备动作可能不可逆）。`command_id` 由 Adapter 生成（从 execution 上下文派生，非随机——确定性可审计）。**本阶段不实现 retry 引擎**——只把键设计进 schema。

## 11. Security Boundary（问题十五）

设备侧默认拒绝链：未知 command → reject；未知 operation → reject；未知 capability → reject；未知 device → reject；非法参数 → reject；schema 错 → reject。固件不执行：未知命令、自由文本、任意代码、服务器下发的 shell。

服务端侧（已存在，Phase 13 实证）：LLM→ESP32 无路径（execution/ 零 LLM，code-only 扫描）；ActionIntent 无法绕过 Gateway（唯一入口+存储链校验）；Capability 无法绕过 Policy（Gateway 内 policy.decide 单点，adapter 模块零 policy import）；断路器代码级常量无 setter。

## 12. Provenance（问题九）

DeviceCommand 携带 provenance 四 id（action/decision/evaluation/strategy）——设备执行结果回传后可完整追溯"为什么执行、谁选择、什么能力、哪个适配器、什么授权"。**本阶段不新增数据库**：DeviceCommand 是传输层瞬态对象，不持久化；执行历史仍由 ExecutionResult 承载。

## 13. 明确禁止项（问题十六清单）

ESP-IDF 初始化 / 编译烧录 / USB-Serial / Wi-Fi / BLE / MQTT / HTTP / WebSocket / GPIO / 电机舵机 LED 音频 / 真实设备 / 设备注册系统 / OTA / Fleet / Retry Engine / Workflow Engine——全部记入 **Future Phase**，不实现。

## 14. Phase 15 建议（可插拔性结论，问题十七）

现有接缝已足够：`AdapterRegistry.register(ESP32Adapter)` 即插即用；FakeExecutionAdapter 保留不动。Phase 15 建议顺序：① DeviceCommand schema + 验证器（纯数据层，可全测）→ ② Device Protocol 编解码（确定性，可全测）→ ③ Transport 接口定义 + MockTransport（无真实通信）→ ④ ESP32Adapter 骨架（构造 DeviceCommand，不接 transport）→ ⑤ 固件协议文档。真实连接放最后且需断路器翻转决策。

## 15. 风险清单

| 级别 | 风险 | 缓解 |
|---|---|---|
| HIGH | 断路器一旦被翻转，无授权源仍会 DENY（Policy 已实现）——风险在于未来某阶段加入授权源时误开 | Phase 15+ 引入授权源必须配套双人审批式代码审查 + 测试断言 |
| MEDIUM | DeviceCommand operation 集随需求膨胀为事实上的自然语言 | operation 枚举纳入 Device Protocol 版本化 review |
| MEDIUM | Transport 选型过早耦合（如直接写死 MQTT） | 严格保持 Transport 接口抽象 + Mock 先行 |
| LOW | device_id 与 IP 混用回潮 | 命名分域：`device_id` 业务身份、`transport_addr` 传输地址 |
| LOW | 固件端 schema 校验滞后于服务端演进 | Device Protocol version 字段强制 + 固件拒未知 version |

## 16. 成功标准核对（§二十全部满足）

ESP32 Adapter/DeviceCommand/Transport/Firmware 职责明确、四层无重叠 ✓ LLM 无设备控制权 ✓ ActionIntent 不能绕过 Gateway ✓ Capability 不能绕过 Policy ✓ DeviceCommand 非自然语言 ✓ closed schema ✓ 未来可接 ESP32（注册即插）✓ 当前无真实 ESP32/通信/副作用 ✓

**Phase 14: PASS（ARCHITECTURE REVIEW）——代码修改 NO，真实通信 0，真实副作用 0，ESP32 NOT CONNECTED。**
