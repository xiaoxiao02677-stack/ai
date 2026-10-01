/*
 * ESP32-S3 Device Protocol Firmware (Phase 19 / 19-R) — ESP-IDF v6.1
 * port. Logic is 1:1 with the Arduino .ino: same validation pipeline
 * (envelope -> version -> closed schema -> device_id -> operation ->
 * parameters -> provenance), same SET_LED strictness ({'on': bool}),
 * same finite idempotency cache, same typed ACK/NACK. Only the
 * framework APIs differ (BSD sockets + cJSON).
 *
 * It is a Device Protocol CONSUMER — it knows nothing about
 * LLM/AI/prompt/personality/memory/decision/strategy/lesson/
 * reflection/conversation/agent/tool/MCP, by design.
 *
 * The ONLY hardware it drives is the SET_LED capability (a specific
 * capability, never a generic GPIO interface).
 */

#ifndef DEVICE_PROTOCOL_MAIN_H
#define DEVICE_PROTOCOL_MAIN_H

/* ---- explicit configuration (edit before flashing; no provisioning) */
#define WIFI_SSID       "YOUR_SSID"        /* TODO: set before flashing */
#define WIFI_PASS       "YOUR_PASS"        /* TODO: set before flashing */
#define DEVICE_ID       "esp32-test-001"   /* business identity (not an IP) */
#define DEVICE_TYPE     "esp32-s3-devkitc1.v1"
#define FIRMWARE_VERSION "0.4.0-p19r-idf"
#define TCP_PORT        3333

/* Phase 19: the FIRST body capability — LED on GPIO 2. The official
 * DevKitC-1 has no discrete GPIO2 LED (its RGB is a WS2812); physical
 * verification therefore uses an external LED on GPIO2 (anode) -> GND
 * (with series resistor). The command path is identical either way. */
#define LED_PIN         2

/* ---- protocol constants (aligned with device_protocol/command.py) */
#define PROTOCOL_VERSION      1
#define MAX_PARAMS            5
#define MAX_PARAM_LEN         200
#define MAX_FRAME_LEN         4096
#define IDEMPOTENCY_CACHE_SIZE 64
#define HEARTBEAT_INTERVAL_MS 10000

#endif /* DEVICE_PROTOCOL_MAIN_H */
