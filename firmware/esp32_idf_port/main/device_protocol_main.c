/*
 * ESP32-S3 Device Protocol Firmware — ESP-IDF v6.1 port (Phase 19-R).
 * Logic 1:1 with firmware/esp32/esp32_device_protocol.ino:
 *   - newline-framed JSON over TCP
 *   - validation pipeline: envelope -> version -> closed schema ->
 *     device_id (no wildcards) -> operation enum -> parameters ->
 *     provenance(4 ids)
 *   - SET_LED strictly {'on': bool} (size==1, key, type)
 *   - finite idempotency cache (duplicate -> NACK, no re-execution)
 *   - typed ACK/NACK envelopes (the shared 9-code enum)
 *   - DEVICE_HELLO + CAPABILITY_ADVERTISEMENT on connect, HEARTBEAT
 *     every 10s, heartbeat_ack consumed silently
 * Framework-API equivalences: WiFiServer->BSD socket, ArduinoJson->
 * cJSON, digitalWrite->gpio_set_level (the SAME single-capability LED
 * action — IDF's name for the identical operation).
 *
 * Device Protocol CONSUMER — never an AI consumer. Zero business
 * hardware beyond the SET_LED capability. Fail closed everywhere.
 */

#include <string.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "cJSON.h"
#include "device_protocol.h"

static const char *TAG = "devproto";

/* ---- finite idempotency cache (same as the .ino) ---- */
static char idem_cache[IDEMPOTENCY_CACHE_SIZE][48];
static int idem_head = 0;

static bool is_duplicate(const char *command_id) {
    for (int i = 0; i < IDEMPOTENCY_CACHE_SIZE; i++) {
        if (idem_cache[i][0] && strcmp(idem_cache[i], command_id) == 0)
            return true;
    }
    return false;
}

static void remember_command_id(const char *command_id) {
    strncpy(idem_cache[idem_head], command_id,
            sizeof(idem_cache[0]) - 1);
    idem_cache[idem_head][sizeof(idem_cache[0]) - 1] = 0;
    idem_head = (idem_head + 1) % IDEMPOTENCY_CACHE_SIZE;
}

/* ---- typed ACK/NACK builder (same envelope as ack.py) ---- */
static char *build_ack(const char *command_id, const char *device_id,
                       const char *status, const char *error_code,
                       cJSON *echo) {
    cJSON *doc = cJSON_CreateObject();
    cJSON_AddStringToObject(doc, "command_id", command_id);
    cJSON_AddStringToObject(doc, "device_id", device_id);
    cJSON_AddStringToObject(doc, "status", status);
    if (error_code && error_code[0])
        cJSON_AddStringToObject(doc, "error_code", error_code);
    else
        cJSON_AddNullToObject(doc, "error_code");
    cJSON_AddNumberToObject(doc, "protocol_version", PROTOCOL_VERSION);
    cJSON_AddStringToObject(doc, "message_type", "ack");
    cJSON_AddItemToObject(doc, "echo", echo ? echo : cJSON_CreateObject());
    cJSON_AddNumberToObject(doc, "created_at",
                            (double)(xTaskGetTickCount() * portTICK_PERIOD_MS) / 1000.0);
    char *out = cJSON_PrintUnformatted(doc);
    cJSON_Delete(doc);
    return out;   /* caller frees */
}

static void send_ack(int fd, const char *command_id, const char *status,
                     const char *error_code, cJSON *echo) {
    char *msg = build_ack(command_id, DEVICE_ID, status, error_code, echo);
    if (msg) {
        strcat(msg, "\n");
        send(fd, msg, strlen(msg), 0);
        cJSON_free(msg);
    }
    ESP_LOGI(TAG, "[TX] cmd=%s status=%s err=%s",
             command_id, status, (error_code && error_code[0]) ? error_code : "-");
}

/* ---- validation pipeline (1:1 with validateCommand in the .ino) ----
 * Returns the error code string or NULL when valid. Out-params carry
 * the parsed command fields for execution. */
static const char *validate_command(const cJSON *doc, char *out_cmd_id,
                                    size_t id_len, char *out_operation,
                                    size_t op_len, cJSON **out_params) {
    if (!cJSON_IsObject(doc)) return "MALFORMED_MESSAGE";
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(doc, "v");
    const cJSON *cmd = cJSON_GetObjectItemCaseSensitive(doc, "cmd");
    if (!cJSON_IsNumber(v) || !cJSON_IsObject(cmd))
        return "INVALID_SCHEMA";
    if ((int)v->valuedouble != PROTOCOL_VERSION)
        return "UNSUPPORTED_VERSION";

    /* closed schema: known keys only */
    const cJSON *iter = NULL;
    cJSON_ArrayForEach(iter, cmd) {
        const char *k = iter->string;
        if (strcmp(k, "command_id") && strcmp(k, "device_id") &&
            strcmp(k, "capability") && strcmp(k, "operation") &&
            strcmp(k, "parameters") && strcmp(k, "protocol_version") &&
            strcmp(k, "provenance") && strcmp(k, "created_at"))
            return "INVALID_SCHEMA";
    }

    const cJSON *j_cmd_id = cJSON_GetObjectItemCaseSensitive(cmd, "command_id");
    const cJSON *j_dev = cJSON_GetObjectItemCaseSensitive(cmd, "device_id");
    const cJSON *j_cap = cJSON_GetObjectItemCaseSensitive(cmd, "capability");
    const cJSON *j_op = cJSON_GetObjectItemCaseSensitive(cmd, "operation");
    const cJSON *j_ver = cJSON_GetObjectItemCaseSensitive(cmd, "protocol_version");
    const cJSON *j_prov = cJSON_GetObjectItemCaseSensitive(cmd, "provenance");
    if (!cJSON_IsString(j_cmd_id) || !cJSON_IsString(j_dev) ||
        !cJSON_IsString(j_cap) || !cJSON_IsString(j_op) ||
        !cJSON_IsNumber(j_ver) || !cJSON_IsObject(j_prov))
        return "INVALID_SCHEMA";

    strncpy(out_cmd_id, j_cmd_id->valuestring, id_len - 1);
    out_cmd_id[id_len - 1] = 0;
    strncpy(out_operation, j_op->valuestring, op_len - 1);
    out_operation[op_len - 1] = 0;

    /* device identity (no wildcards) */
    if (strcmp(j_dev->valuestring, DEVICE_ID) != 0)
        return "UNKNOWN_DEVICE";
    /* protocol version (again, inside the command) */
    if ((int)j_ver->valuedouble != PROTOCOL_VERSION)
        return "UNSUPPORTED_VERSION";
    /* provenance: 4 required ids */
    const char *prov_keys[] = {"action_id", "decision_id",
                               "evaluation_id", "strategy_id"};
    for (int i = 0; i < 4; i++) {
        const cJSON *pv = cJSON_GetObjectItemCaseSensitive(j_prov, prov_keys[i]);
        if (!cJSON_IsString(pv) || !pv->valuestring[0])
            return "INVALID_SCHEMA";
    }
    /* operation: controlled enum (TEST_ECHO protocol test; SET_LED the
     * first body operation — Phase 19) */
    if (strcmp(out_operation, "TEST_ECHO") != 0 &&
        strcmp(out_operation, "SET_LED") != 0)
        return "UNKNOWN_OPERATION";

    /* parameters: closed set. SET_LED is STRICTLY {'on': bool}. */
    const cJSON *params = cJSON_GetObjectItemCaseSensitive(cmd, "parameters");
    if (params) {
        if (!cJSON_IsObject(params)) return "INVALID_PARAMETERS";
        if (strcmp(out_operation, "SET_LED") == 0) {
            if (cJSON_GetArraySize(params) != 1) return "INVALID_PARAMETERS";
            const cJSON *on = cJSON_GetObjectItemCaseSensitive(params, "on");
            if (!cJSON_IsBool(on)) return "INVALID_PARAMETERS";
        } else {
            if (cJSON_GetArraySize(params) > MAX_PARAMS)
                return "INVALID_PARAMETERS";
            const cJSON *pi = NULL;
            cJSON_ArrayForEach(pi, params) {
                if (!cJSON_IsString(pi) ||
                    strlen(pi->valuestring) > MAX_PARAM_LEN)
                    return "INVALID_PARAMETERS";
            }
        }
        *out_params = cJSON_Duplicate(params, 1);
    } else {
        *out_params = NULL;
    }
    return NULL;   /* valid */
}

/* ---- per-connection handling (1:1 with handleClient in the .ino) ---- */
static void handle_connection(int fd) {
    /* session handshake: HELLO + capability advertisement */
    {
        cJSON *hello = cJSON_CreateObject();
        cJSON_AddStringToObject(hello, "message_type", "device_hello");
        cJSON_AddNumberToObject(hello, "protocol_version", PROTOCOL_VERSION);
        cJSON_AddStringToObject(hello, "device_id", DEVICE_ID);
        cJSON_AddStringToObject(hello, "device_type", DEVICE_TYPE);
        cJSON_AddStringToObject(hello, "firmware_version", FIRMWARE_VERSION);
        char *out = cJSON_PrintUnformatted(hello);
        strcat(out, "\n");
        send(fd, out, strlen(out), 0);
        cJSON_free(out);
        cJSON_Delete(hello);
        ESP_LOGI(TAG, "[TX] device_hello");

        cJSON *adv = cJSON_CreateObject();
        cJSON_AddStringToObject(adv, "message_type",
                                "capability_advertisement");
        cJSON_AddNumberToObject(adv, "protocol_version", PROTOCOL_VERSION);
        cJSON_AddStringToObject(adv, "device_id", DEVICE_ID);
        cJSON *ops = cJSON_AddArrayToObject(adv, "operations");
        cJSON_AddItemToArray(ops, cJSON_CreateString("TEST_ECHO"));
        cJSON_AddItemToArray(ops, cJSON_CreateString("SET_LED"));
        out = cJSON_PrintUnformatted(adv);
        strcat(out, "\n");
        send(fd, out, strlen(out), 0);
        cJSON_free(out);
        cJSON_Delete(adv);
        ESP_LOGI(TAG, "[TX] capability_advertisement");
    }

    TickType_t last_beat = xTaskGetTickCount();
    char frame[MAX_FRAME_LEN + 1];
    size_t flen = 0;
    char rbuf[1024];

    for (;;) {
        /* heartbeat: transport liveness only, carries no AI state */
        if ((xTaskGetTickCount() - last_beat) * portTICK_PERIOD_MS
                >= HEARTBEAT_INTERVAL_MS) {
            last_beat = xTaskGetTickCount();
            cJSON *hb = cJSON_CreateObject();
            cJSON_AddStringToObject(hb, "message_type", "heartbeat");
            cJSON_AddNumberToObject(hb, "protocol_version", PROTOCOL_VERSION);
            cJSON_AddStringToObject(hb, "device_id", DEVICE_ID);
            char *out = cJSON_PrintUnformatted(hb);
            strcat(out, "\n");
            send(fd, out, strlen(out), 0);
            cJSON_free(out);
            cJSON_Delete(hb);
            ESP_LOGI(TAG, "[TX] heartbeat");
        }
        int n = recv(fd, rbuf, sizeof(rbuf), MSG_DONTWAIT);
        if (n == 0) break;                    /* peer closed */
        if (n < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                vTaskDelay(pdMS_TO_TICKS(5));
                continue;
            }
            break;                             /* error: drop connection */
        }
        for (int i = 0; i < n; i++) {
            char c = rbuf[i];
            if (c != '\n') {
                if (flen < MAX_FRAME_LEN) frame[flen++] = c;
                continue;
            }
            frame[flen] = 0;
            flen = 0;
            if (frame[0] == 0) continue;       /* keepalive */

            ESP_LOGI(TAG, "[RX] %d bytes", (int)strlen(frame));
            cJSON *doc = cJSON_Parse(frame);
            if (!doc) {
                send_ack(fd, "", "NACK", "MALFORMED_MESSAGE", NULL);
                continue;
            }
            /* server->device session messages carry no command —
             * heartbeat_ack is consumed silently (no action) */
            const cJSON *mt = cJSON_GetObjectItemCaseSensitive(doc,
                                                                "message_type");
            if (cJSON_IsString(mt) &&
                strcmp(mt->valuestring, "heartbeat_ack") == 0) {
                ESP_LOGI(TAG, "[RX] heartbeat_ack (ignored)");
                cJSON_Delete(doc);
                continue;
            }

            char cmd_id[64] = "", operation[32] = "";
            cJSON *params = NULL;
            const char *error = validate_command(doc, cmd_id,
                                                 sizeof(cmd_id), operation,
                                                 sizeof(operation), &params);
            if (error) {
                ESP_LOGI(TAG, "[VAL] cmd=%s -> NACK %s", cmd_id, error);
                send_ack(fd, cmd_id, "NACK", error, NULL);
                cJSON_Delete(doc);
                continue;
            }
            /* idempotency: duplicate -> NACK, no re-execution */
            if (is_duplicate(cmd_id)) {
                ESP_LOGI(TAG, "[IDEM] duplicate %s -> NACK", cmd_id);
                send_ack(fd, cmd_id, "NACK", "DUPLICATE_COMMAND", NULL);
                if (params) cJSON_Delete(params);
                cJSON_Delete(doc);
                continue;
            }
            /* execute: TEST_ECHO echoes params; SET_LED drives the LED
             * (the ONE sanctioned body action — gpio_set_level is the
             * IDF equivalent of the .ino's digitalWrite on LED_PIN) */
            remember_command_id(cmd_id);
            if (strcmp(operation, "SET_LED") == 0 && params) {
                const cJSON *on = cJSON_GetObjectItemCaseSensitive(params,
                                                                   "on");
                bool led_on = cJSON_IsTrue(on);
                gpio_set_level(LED_PIN, led_on ? 1 : 0);   /* body action */
                ESP_LOGI(TAG, "[EXE] cmd=%s op=SET_LED on=%d",
                         cmd_id, led_on ? 1 : 0);
            } else {
                ESP_LOGI(TAG, "[EXE] cmd=%s op=TEST_ECHO", cmd_id);
            }
            send_ack(fd, cmd_id, "ACK", "", params);
            cJSON_Delete(doc);   /* params ownership moved into the ack */
        }
    }
    close(fd);
}

/* ---- WiFi + main ---- */
static void wifi_init_sta(void) {
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    wifi_config_t wifi_config = { 0 };
    strcpy((char *)wifi_config.sta.ssid, WIFI_SSID);
    strcpy((char *)wifi_config.sta.password, WIFI_PASS);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_LOGI(TAG, "[NET] connecting to %s ...", WIFI_SSID);
    /* fail closed: without network there is no session and no commands */
    int wait = 0;
    while (esp_wifi_connect() != ESP_OK ||
           (wait++ < 100 && esp_wifi_sta_get_ap_info(NULL) != ESP_OK)) {
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}

void app_main(void) {
    ESP_LOGI(TAG, "[BOOT] device_id=%s port=%d fw=%s",
             DEVICE_ID, TCP_PORT, FIRMWARE_VERSION);
    /* the ONLY hardware this firmware drives: LED output, fail-safe
     * OFF at boot (same as the .ino) */
    gpio_reset_pin(LED_PIN);
    gpio_set_direction(LED_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level(LED_PIN, 0);

    wifi_init_sta();

    /* the server (behind VMware NAT) connects OUTBOUND to us — the
     * firmware listens, exactly like the .ino's WiFiServer */
    int listen_fd = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
    if (listen_fd < 0) { ESP_LOGE(TAG, "socket failed"); return; }
    int opt = 1;
    setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    struct sockaddr_in dest = {
        .sin_addr.s_addr = htonl(INADDR_ANY),
        .sin_family = AF_INET,
        .sin_port = htons(TCP_PORT),
    };
    if (bind(listen_fd, (struct sockaddr *)&dest, sizeof(dest)) != 0) {
        ESP_LOGE(TAG, "bind failed");
        close(listen_fd);
        return;
    }
    if (listen(listen_fd, 2) != 0) {
        ESP_LOGE(TAG, "listen failed");
        close(listen_fd);
        return;
    }
    esp_netif_ip_info_t ip_info;
    esp_netif_t *netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    if (esp_netif_get_ip_info(netif, &ip_info) == ESP_OK) {
        ESP_LOGI(TAG, "[NET] IP=" IPSTR, IP2STR(&ip_info.ip));
    }
    while (1) {
        struct sockaddr_in source;
        socklen_t len = sizeof(source);
        int fd = accept(listen_fd, (struct sockaddr *)&source, &len);
        if (fd < 0) { vTaskDelay(pdMS_TO_TICKS(50)); continue; }
        ESP_LOGI(TAG, "[NET] connection from %s",
                 inet_ntoa(source.sin_addr));
        handle_connection(fd);
    }
}
