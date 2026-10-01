/*
 * ESP32 Minimal Firmware — Device Protocol Consumer (Phase 16)
 *
 * Receives newline-framed JSON DeviceCommands over TCP, validates
 * EVERYTHING (envelope, version, schema, device_id, operation,
 * parameters, command_id idempotency), executes the single TEST_ECHO
 * operation, and replies with a typed ACK / NACK envelope.
 *
 * This firmware is a PROTOCOL CONSUMER, not an AI consumer: it knows
 * nothing about LLM/prompt/personality/memory/decision/strategy/
 * lesson/reflection/conversation/agent/tool/MCP — by design.
 *
 * Zero business hardware side effects: no GPIO/servo/motor/LED/relay/
 * camera/mic/speaker control. The only "hardware" in use is Wi-Fi +
 * TCP (the transport itself).
 *
 * Build/flash commands: see README.md (manual only — never automatic).
 */

#include <WiFi.h>
#include <ArduinoJson.h>

// ---- explicit configuration (edit before flashing; no provisioning) ----
const char* WIFI_SSID = "YOUR_SSID";      // TODO: set before flashing
const char* WIFI_PASS = "YOUR_PASS";      // TODO: set before flashing
const char* DEVICE_ID  = "esp32-test-001"; // business identity (not an IP)
const uint16_t TCP_PORT = 3333;

// ---- protocol constants (aligned with device_protocol/command.py) ----
const int  PROTOCOL_VERSION = 1;
const int  MAX_PARAMS = 5;
const int  MAX_PARAM_LEN = 200;
const int  MAX_FRAME_LEN = 4096;
const int  IDEMPOTENCY_CACHE_SIZE = 64;

WiFiServer server(TCP_PORT);

// finite idempotency cache: command_ids already executed
String idemCache[IDEMPOTENCY_CACHE_SIZE];
int idemHead = 0;

bool isDuplicate(const String& commandId) {
  for (int i = 0; i < IDEMPOTENCY_CACHE_SIZE; i++) {
    if (idemCache[i].length() && idemCache[i] == commandId) return true;
  }
  return false;
}

void rememberCommandId(const String& commandId) {
  idemCache[idemHead] = commandId;
  idemHead = (idemHead + 1) % IDEMPOTENCY_CACHE_SIZE;
}

// ---- typed ACK / NACK builder ----
String buildAck(const String& commandId, const String& deviceId,
                const String& status, const String& errorCode,
                const JsonObject& echo) {
  StaticJsonDocument<1024> doc;
  doc["command_id"] = commandId;
  doc["device_id"] = deviceId;
  doc["status"] = status;             // "ACK" | "NACK"
  doc["error_code"] = errorCode;      // null on success
  doc["protocol_version"] = PROTOCOL_VERSION;
  doc["message_type"] = "ack";
  JsonObject echoObj = doc.createNestedObject("echo");
  for (JsonPair kv : echo) echoObj[kv.key()] = kv.value();
  doc["created_at"] = (float)(millis() / 1000.0);
  String out;
  serializeJson(doc, out);
  return out;
}

void sendAck(WiFiClient& client, const String& commandId,
             const String& deviceId, const String& status,
             const String& errorCode, const JsonObject& echo) {
  String msg = buildAck(commandId, deviceId, status, errorCode, echo);
  client.print(msg + "\n");
  Serial.printf("[TX] cmd=%s status=%s err=%s\n",
                commandId.c_str(), status.c_str(),
                errorCode.length() ? errorCode.c_str() : "-");
}

// ---- validation pipeline: returns error_code or "" when valid ----
String validateCommand(const StaticJsonDocument<2048>& doc,
                       String& outCommandId, String& outOperation,
                       JsonObject& outParams, String& outCapability) {
  // envelope
  if (!doc.is<JsonObject>()) return "MALFORMED_MESSAGE";
  JsonObject root = doc.as<JsonObject>();
  if (!root.containsKey("v") || !root.containsKey("cmd"))
    return "INVALID_SCHEMA";
  if (root["v"] != PROTOCOL_VERSION) return "UNSUPPORTED_VERSION";
  JsonObject cmd = root["cmd"];
  if (cmd.isNull()) return "INVALID_SCHEMA";

  // closed schema: known keys only
  for (JsonPair kv : cmd) {
    String k = kv.key().c_str();
    if (k != "command_id" && k != "device_id" && k != "capability" &&
        k != "operation" && k != "parameters" && k != "protocol_version" &&
        k != "provenance" && k != "created_at")
      return "INVALID_SCHEMA";
  }

  // required fields
  if (!cmd.containsKey("command_id") || !cmd.containsKey("device_id") ||
      !cmd.containsKey("capability") || !cmd.containsKey("operation") ||
      !cmd.containsKey("protocol_version") ||
      !cmd.containsKey("provenance"))
    return "INVALID_SCHEMA";

  outCommandId = String((const char*)cmd["command_id"]);
  outCapability = String((const char*)cmd["capability"]);
  outOperation = String((const char*)cmd["operation"]);

  // device identity (no wildcards)
  String devId = String((const char*)cmd["device_id"]);
  if (devId != DEVICE_ID) return "UNKNOWN_DEVICE";

  // protocol version (again, inside the command)
  if (cmd["protocol_version"] != PROTOCOL_VERSION) return "UNSUPPORTED_VERSION";

  // provenance: 4 required ids
  JsonObject prov = cmd["provenance"];
  if (prov.isNull() || !prov.containsKey("action_id") ||
      !prov.containsKey("decision_id") || !prov.containsKey("evaluation_id") ||
      !prov.containsKey("strategy_id"))
    return "INVALID_SCHEMA";

  // operation: controlled enum (TEST_ECHO only in Phase 16)
  if (outOperation != "TEST_ECHO") return "UNKNOWN_OPERATION";

  // parameters: closed set of bounded strings
  if (cmd.containsKey("parameters")) {
    JsonObject params = cmd["parameters"];
    if (!params.isNull()) {
      if (params.size() > MAX_PARAMS) return "INVALID_PARAMETERS";
      for (JsonPair kv : params) {
        if (!kv.value().is<const char*>()) return "INVALID_PARAMETERS";
        String v = String((const char*)kv.value());
        if (v.length() > MAX_PARAM_LEN) return "INVALID_PARAMETERS";
      }
      outParams = params;
    }
  }
  return "";  // valid
}

// ---- per-connection handling ----
void handleClient(WiFiClient client) {
  String frame = "";
  while (client.connected()) {
    while (client.available()) {
      char c = client.read();
      if (c == '\n') {
        if (frame.length() == 0) continue;      // keepalive
        if (frame.length() > MAX_FRAME_LEN) {
          sendAck(client, "", DEVICE_ID, "NACK", "MALFORMED_MESSAGE",
                  *(new StaticJsonDocument<256>().to<JsonObject>()));
          frame = "";
          continue;
        }
        Serial.printf("[RX] %d bytes\n", frame.length());
        StaticJsonDocument<2048> doc;
        DeserializationError err = deserializeJson(doc, frame);
        frame = "";
        if (err) {
          StaticJsonDocument<256> empty;
          sendAck(client, "", DEVICE_ID, "NACK", "MALFORMED_MESSAGE",
                  empty.to<JsonObject>());
          continue;
        }
        String commandId = "", operation = "", capability = "";
        JsonObject params;   // defaults to null JsonObject
        String error = validateCommand(doc, commandId, operation,
                                       params, capability);
        if (error.length()) {
          Serial.printf("[VAL] cmd=%s -> NACK %s\n",
                        commandId.c_str(), error.c_str());
          StaticJsonDocument<256> empty;
          sendAck(client, commandId, DEVICE_ID, "NACK", error,
                  empty.to<JsonObject>());
          continue;
        }
        // idempotency: duplicate -> NACK, no re-execution
        if (isDuplicate(commandId)) {
          Serial.printf("[IDEM] duplicate %s -> NACK\n", commandId.c_str());
          StaticJsonDocument<256> empty;
          sendAck(client, commandId, DEVICE_ID, "NACK",
                  "DUPLICATE_COMMAND", empty.to<JsonObject>());
          continue;
        }
        // execute TEST_ECHO: deterministic echo of the parameters
        rememberCommandId(commandId);
        Serial.printf("[EXE] cmd=%s op=TEST_ECHO params=%d\n",
                      commandId.c_str(), params.size());
        sendAck(client, commandId, DEVICE_ID, "ACK", "", params);
        continue;
      } else {
        frame += c;
      }
    }
    delay(2);
  }
  client.stop();
}

void setup() {
  Serial.begin(115200);
  Serial.printf("[BOOT] device_id=%s port=%d\n", DEVICE_ID, TCP_PORT);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  int wait = 0;
  while (WiFi.status() != WL_CONNECTED && wait < 200) {
    delay(100); wait++;
  }
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[NET] Wi-Fi connect failed — halting (fail closed)");
    while (true) delay(1000);
  }
  Serial.print("[NET] IP=");
  Serial.println(WiFi.localIP());   // transport address (NOT the identity)
  server.begin();
}

void loop() {
  WiFiClient client = server.available();
  if (client) {
    handleClient(client);
  }
  delay(2);
}
