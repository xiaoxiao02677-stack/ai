# P19 Device Protocol Firmware — ESP-IDF v6.1 port for ESP32-S3
# Logic 1:1 with firmware/esp32/esp32_device_protocol.ino (Arduino):
# same protocol, same validation pipeline, same SET_LED strictness,
# same idempotency, same ACK/NACK semantics. Framework API adapted to
# ESP-IDF (WiFiServer->socket, ArduinoJson->cJSON) — NO protocol
# changes. It remains a Device Protocol CONSUMER, never an AI consumer.
#
# Board: ESP32-S3-DevKitC-1 (official)
# LED  : the onboard addressable RGB is WS2812 — P19 defines SET_LED on
#        GPIO2 as the capability. For boards without a GPIO2 LED the
#        physical verification requires an external LED; the command
#        path (validation + ACK) is identical either way.

idf_component_register(SRCS "device_protocol_main.c"
                       INCLUDE_DIRS "."
                       REQUIRES nvs_flash esp_wifi)

# protocol constants live in the header for testability
target_compile_options(${COMPONENT_LIB} PRIVATE -Wno-format)
