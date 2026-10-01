# ESP32 Minimal Firmware — Device Protocol Consumer (Phase 16)

## ⚠️ 本固件是 Device Protocol Consumer，不是 AI Consumer

固件只知道：接收字节 → 解码 → 验证（device_id/version/command_id/operation/parameters）→ 执行 TEST_ECHO → 返回 ACK/NACK。

固件绝不知道：LLM、AI、prompt、personality、memory、decision、strategy、lesson、reflection、conversation、agent、tool、MCP。

## 硬件副作用声明

**本固件零业务硬件动作**：无 GPIO/舵机/电机/LED/继电器/摄像头/麦克风/扬声器业务控制。
唯一的"硬件"使用是 Wi-Fi 连接与 TCP 监听（Transport 本身）。

## 协议

与服务端共享同一协议定义（`src/open_llm_vtuber/device_protocol/`）：

- **请求**：`{"v": 1, "cmd": {command_id, device_id, capability, operation, parameters, protocol_version, provenance, created_at}}` + `\n`
- **响应**：`{"command_id", "device_id", "status": "ACK"|"NACK", "error_code", "protocol_version": 1, "message_type": "ack", "echo", "created_at"}` + `\n`

## 错误码（与服务端 `ACK_ERROR_CODES` 一致）

`UNKNOWN_COMMAND / DUPLICATE_COMMAND / UNKNOWN_DEVICE / UNKNOWN_OPERATION / INVALID_PARAMETERS / INVALID_SCHEMA / UNSUPPORTED_VERSION / MALFORMED_MESSAGE / TIMEOUT`

## 幂等

`command_id` 唯一幂等键。固件维护有限环形缓冲（`IDEMPOTENCY_CACHE_SIZE`）：
首次收到 → 执行 → ACK；重复收到 → **不重复执行** → NACK(DUPLICATE_COMMAND)。

## 手动构建与烧写（不自动执行）

```bash
# Arduino CLI（需自行安装工具链；服务器当前未安装）
arduino-cli compile --fqbn esp32:esp32:esp32 firmware/esp32
arduino-cli upload -p /dev/ttyUSB0 --fqbn esp32:esp32:esp32 firmware/esp32

# 或 esptool
esptool.py --chip esp32 --port /dev/ttyUSB0 write_flash 0x1000 firmware.bin
```

**本阶段固件状态：Built = NO / Flashed = NO（服务器无工具链与设备，见验收报告）**

## 配置

固件内置常量（刷写前手动编辑）：
- `WIFI_SSID` / `WIFI_PASS`：局域网（无 provisioning，无 BLE 配网）
- `DEVICE_ID`：`esp32-test-001`（业务身份，非 IP）
- `TCP_PORT`：`3333`

## 端口暴露

`DeviceProtocolValidator` 类已按服务端逻辑 1:1 移植（C++ 版），可用于未来单元测试。
