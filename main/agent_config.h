// main/agent_config.h —— 持久配置:Wi-Fi 与服务器地址。存 NVS 命名空间 "agent"。
//
// 未保存过的项取 menuconfig(Kconfig)里的默认值。通常用 tools/usb_link.py 通过 USB
// 写入(见 README),无需重新编译固件;Wi-Fi 为空时只使用 USB 链路。
#pragma once

#include "esp_err.h"
#include <stdint.h>

typedef struct {
    char ssid[33];
    char pass[65];
    char host[64];
    uint16_t port;
} agent_config_t;

// 读取配置。NVS 读失败时回落到 Kconfig 默认值,不报错。
void agent_config_load(agent_config_t *cfg);

// 保存一项:key 为 ssid / pass / host / port。重启后生效。
// 未知 key 返回 ESP_ERR_NOT_FOUND;值过长或 port 非法返回 ESP_ERR_INVALID_ARG。
esp_err_t agent_config_set(const char *key, const char *value);
