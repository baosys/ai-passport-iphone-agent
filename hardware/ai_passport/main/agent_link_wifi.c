#include "agent_link.h"

#include "agent_bridge.h"

#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "lwip/netdb.h"
#include "lwip/sockets.h"

static const char *TAG = "link_wifi";

#define GOT_IP BIT0
#define LINE_MAX_LEN 256

static agent_config_t s_cfg;
static EventGroupHandle_t s_events;
static char s_ip[16];

static void on_wifi(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_events, GOT_IP);
        s_ip[0] = '\0';
        agent_bridge_set_link(AGENT_LINK_WIFI, AGENT_LINK_WAITING, "joining Wi-Fi");
        esp_wifi_connect();  // 驱动自带退避;断线后持续重连
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *ev = data;
        snprintf(s_ip, sizeof(s_ip), IPSTR, IP2STR(&ev->ip_info.ip));
        ESP_LOGI(TAG, "got ip %s", s_ip);
        xEventGroupSetBits(s_events, GOT_IP);
    }
}

static int connect_server(void)
{
    char port[8];
    snprintf(port, sizeof(port), "%u", s_cfg.port);
    const struct addrinfo hints = { .ai_family = AF_INET, .ai_socktype = SOCK_STREAM };
    struct addrinfo *res = NULL;
    if (getaddrinfo(s_cfg.host, port, &hints, &res) != 0 || !res) return -1;
    int fd = socket(res->ai_family, res->ai_socktype, 0);
    if (fd >= 0 && connect(fd, res->ai_addr, res->ai_addrlen) != 0) {
        close(fd);
        fd = -1;
    }
    freeaddrinfo(res);
    if (fd < 0) return -1;
    int one = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));  // 每条回复都很短,不能等 Nagle
    setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &one, sizeof(one));
    return fd;
}

static bool send_line(int fd, const char *text)
{
    char out[160];
    int n = snprintf(out, sizeof(out), "%s\n", text);
    if (n >= (int)sizeof(out)) n = sizeof(out) - 1;
    return send(fd, out, n, 0) == n;
}

// 一个服务器连接的生命周期:hello,然后逐行执行直到断开。
static void serve(int fd)
{
    char line[LINE_MAX_LEN], reply[128], buf[128];
    size_t len = 0;
    bool overflow = false;
    agent_bridge_hello(reply, sizeof(reply));
    if (!send_line(fd, reply)) return;
    for (;;) {
        int n = recv(fd, buf, sizeof(buf), 0);
        if (n <= 0) return;
        for (int i = 0; i < n; i++) {
            char c = buf[i];
            if (c == '\r') continue;
            if (c != '\n') {
                if (len < sizeof(line) - 1) line[len++] = c; else overflow = true;
                continue;
            }
            line[len] = '\0';
            if (overflow) {
                snprintf(reply, sizeof(reply), "err line too long");
            } else if (len) {
                agent_bridge_handle(AGENT_LINK_WIFI, line, reply, sizeof(reply));
            }
            if ((overflow || len) && !send_line(fd, reply)) return;
            len = 0;
            overflow = false;
        }
    }
}

static void wifi_task(void *arg)
{
    (void)arg;
    char detail[96];
    for (;;) {
        xEventGroupWaitBits(s_events, GOT_IP, pdFALSE, pdTRUE, portMAX_DELAY);
        // 服务器只接受一块板子,新连接会挤掉旧连接。USB 链路在线时 Wi-Fi 让路,
        // 否则两条链路连向同一服务器时会互相踢下线。
        if (agent_bridge_link_state(AGENT_LINK_USB) == AGENT_LINK_UP) {
            agent_bridge_set_link(AGENT_LINK_WIFI, AGENT_LINK_WAITING, "standby: USB link active");
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }
        snprintf(detail, sizeof(detail), "%s > %s:%u", s_ip, s_cfg.host, s_cfg.port);
        agent_bridge_set_link(AGENT_LINK_WIFI, AGENT_LINK_WAITING, detail);
        int fd = connect_server();
        if (fd < 0) {
            vTaskDelay(pdMS_TO_TICKS(1000));  // 服务器还没起来:稍后再试
            continue;
        }
        ESP_LOGI(TAG, "server %s:%u connected", s_cfg.host, s_cfg.port);
        agent_bridge_set_link(AGENT_LINK_WIFI, AGENT_LINK_UP, detail);
        serve(fd);
        close(fd);
        ESP_LOGI(TAG, "server link closed");
        // 断开时松开按键:被截断的手势不能变成长按。
        agent_bridge_link_lost(AGENT_LINK_WIFI);
        agent_bridge_set_link(AGENT_LINK_WIFI, AGENT_LINK_WAITING, detail);
        vTaskDelay(pdMS_TO_TICKS(200));
    }
}

esp_err_t agent_link_wifi_start(const agent_config_t *cfg)
{
    if (!cfg->ssid[0] || !cfg->host[0]) {
        agent_bridge_set_link(AGENT_LINK_WIFI, AGENT_LINK_OFF, "not configured");
        return ESP_OK;
    }
    s_cfg = *cfg;
    s_events = xEventGroupCreate();
    if (!s_events) return ESP_ERR_NO_MEM;

    esp_err_t err = esp_netif_init();
    if (err == ESP_OK) err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) return err;
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    err = esp_wifi_init(&init);
    if (err != ESP_OK) return err;
    esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi, NULL);
    esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_wifi, NULL);

    wifi_config_t wc = { 0 };
    // ssid / password 字段可以恰好填满、不带结尾 '\0',所以按长度拷贝。
    memcpy(wc.sta.ssid, cfg->ssid, strnlen(cfg->ssid, sizeof(wc.sta.ssid)));
    memcpy(wc.sta.password, cfg->pass, strnlen(cfg->pass, sizeof(wc.sta.password)));
    wc.sta.threshold.authmode = cfg->pass[0] ? WIFI_AUTH_WPA_PSK : WIFI_AUTH_OPEN;
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &wc);
    err = esp_wifi_start();
    if (err != ESP_OK) return err;
    // 省电模式下命令要等 DTIM 才收到(约 100 ms 一条)。与 BLE 共存时 Wi-Fi 仍只在
    // 自己的时间片收发,但关掉省电可去掉这段等待。
    esp_wifi_set_ps(WIFI_PS_NONE);

    agent_bridge_set_link(AGENT_LINK_WIFI, AGENT_LINK_WAITING, "joining Wi-Fi");
    if (xTaskCreate(wifi_task, "link_wifi", 4096, NULL, 5, NULL) != pdPASS) return ESP_ERR_NO_MEM;
    return ESP_OK;
}
