#include "agent_link.h"

#include "agent_bridge.h"
#include "agent_config.h"
#include "agent_hid.h"

#include <stdio.h>
#include <string.h>

#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "link_usb";

#define LINE_MAX_LEN 256
// 服务器空闲时每 2 s ping 一次;超过这么久没有任何命令就视为桥接脚本已退出。
#define IDLE_TIMEOUT_US (10LL * 1000 * 1000)

static bool s_up;

static void reply(const char *text)
{
    // 与 ESP_LOG 共用 stdout:一次 printf 写完整行,避免与日志交错。
    printf("@%s\n", text);
    fflush(stdout);
}

static void set_up(bool up)
{
    if (s_up == up) return;
    s_up = up;
    if (!up) agent_bridge_link_lost(AGENT_LINK_USB);
    agent_bridge_set_link(AGENT_LINK_USB, up ? AGENT_LINK_UP : AGENT_LINK_WAITING, NULL);
}

// 只走 USB 的本地管理命令:配置、配对管理。返回 false 表示不是管理命令。
static bool handle_local(char *line)
{
    char buf[96];
    if (strcmp(line, "hello") == 0) {
        set_up(true);
        agent_bridge_hello(buf, sizeof(buf));
        reply(buf);
        return true;
    }
    if (strcmp(line, "bye") == 0) {
        set_up(false);
        reply("ok");
        return true;
    }
    if (strcmp(line, "config") == 0) {
        agent_config_t cfg;
        agent_config_load(&cfg);
        static const char *const HID[] = { "off", "advertising", "connected", "ready" };
        char out[200];
        snprintf(out, sizeof(out), "config ssid=%s pass=%s host=%s port=%u phone=%s",
                 cfg.ssid, cfg.pass[0] ? "(set)" : "", cfg.host, cfg.port, HID[agent_hid_state()]);
        reply(out);
        return true;
    }
    if (strncmp(line, "set ", 4) == 0) {
        char *key = line + 4;
        char *value = strchr(key, ' ');
        if (value) *value++ = '\0';
        esp_err_t err = agent_config_set(key, value ? value : "");
        if (err == ESP_OK) {
            reply("ok");
        } else {
            snprintf(buf, sizeof(buf), "err %s", err == ESP_ERR_NOT_FOUND ? "unknown setting" : esp_err_to_name(err));
            reply(buf);
        }
        return true;
    }
    if (strcmp(line, "forget") == 0) {
        reply(agent_hid_forget() == ESP_OK ? "ok" : "err forget failed");
        return true;
    }
    if (strcmp(line, "reboot") == 0) {
        reply("ok");
        vTaskDelay(pdMS_TO_TICKS(100));
        esp_restart();
    }
    return false;
}

static void handle_line(char *line)
{
    if (!*line) return;
    if (handle_local(line)) return;
    set_up(true);  // 任何协议命令都说明桥接脚本在线
    char out[128];
    agent_bridge_handle(AGENT_LINK_USB, line, out, sizeof(out));
    reply(out);
}

static void usb_task(void *arg)
{
    (void)arg;
    static char line[LINE_MAX_LEN];
    size_t len = 0;
    bool overflow = false;
    int64_t last_rx = esp_timer_get_time();
    uint8_t buf[64];

    for (;;) {
        int n = usb_serial_jtag_read_bytes(buf, sizeof(buf), pdMS_TO_TICKS(200));
        int64_t now = esp_timer_get_time();
        if (n <= 0) {
            if (s_up && now - last_rx > IDLE_TIMEOUT_US) {
                ESP_LOGI(TAG, "bridge silent, releasing");
                set_up(false);
            }
            continue;
        }
        last_rx = now;
        for (int i = 0; i < n; i++) {
            char c = (char)buf[i];
            if (c == '\r') continue;
            if (c != '\n') {
                if (len < sizeof(line) - 1) {
                    line[len++] = c;
                } else {
                    overflow = true;  // 丢弃整行,不执行被截断的命令
                }
                continue;
            }
            line[len] = '\0';
            if (overflow) {
                reply("err line too long");
            } else {
                handle_line(line);
            }
            len = 0;
            overflow = false;
        }
    }
}

esp_err_t agent_link_usb_start(void)
{
    usb_serial_jtag_driver_config_t cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    cfg.rx_buffer_size = 1024;
    cfg.tx_buffer_size = 1024;
    esp_err_t err = usb_serial_jtag_driver_install(&cfg);
    if (err != ESP_OK) return err;
    // 让 stdout(日志与协议回复)也走驱动,读写共用同一套缓冲,互不抢占硬件 FIFO。
    usb_serial_jtag_vfs_use_driver();
    agent_bridge_set_link(AGENT_LINK_USB, AGENT_LINK_WAITING, NULL);
    if (xTaskCreate(usb_task, "link_usb", 4096, NULL, 5, NULL) != pdPASS) return ESP_ERR_NO_MEM;
    return ESP_OK;
}
