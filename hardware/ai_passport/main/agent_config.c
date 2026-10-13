#include "agent_config.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "nvs.h"
#include "sdkconfig.h"

#define NS "agent"

static void read_str(nvs_handle_t h, const char *key, char *out, size_t len)
{
    size_t n = len;
    if (nvs_get_str(h, key, out, &n) != ESP_OK) {
        // 保留调用方预先填好的默认值。
    }
}

void agent_config_load(agent_config_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    snprintf(cfg->ssid, sizeof(cfg->ssid), "%s", CONFIG_AGENT_WIFI_SSID);
    snprintf(cfg->pass, sizeof(cfg->pass), "%s", CONFIG_AGENT_WIFI_PASSWORD);
    snprintf(cfg->host, sizeof(cfg->host), "%s", CONFIG_AGENT_SERVER_HOST);
    cfg->port = CONFIG_AGENT_SERVER_PORT;

    nvs_handle_t h;
    if (nvs_open(NS, NVS_READONLY, &h) != ESP_OK) return;
    read_str(h, "ssid", cfg->ssid, sizeof(cfg->ssid));
    read_str(h, "pass", cfg->pass, sizeof(cfg->pass));
    read_str(h, "host", cfg->host, sizeof(cfg->host));
    uint16_t port;
    if (nvs_get_u16(h, "port", &port) == ESP_OK && port) cfg->port = port;
    nvs_close(h);
}

esp_err_t agent_config_set(const char *key, const char *value)
{
    const agent_config_t *probe = NULL;
    size_t limit;
    if (strcmp(key, "ssid") == 0) {
        limit = sizeof(probe->ssid);
    } else if (strcmp(key, "pass") == 0) {
        limit = sizeof(probe->pass);
    } else if (strcmp(key, "host") == 0) {
        limit = sizeof(probe->host);
    } else if (strcmp(key, "port") == 0) {
        limit = 0;
    } else {
        return ESP_ERR_NOT_FOUND;
    }

    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READWRITE, &h);
    if (err != ESP_OK) return err;
    if (limit == 0) {
        char *end;
        long port = strtol(value, &end, 10);
        err = (*value && *end == '\0' && port > 0 && port < 65536)
            ? nvs_set_u16(h, key, (uint16_t)port) : ESP_ERR_INVALID_ARG;
    } else {
        err = strlen(value) < limit ? nvs_set_str(h, key, value) : ESP_ERR_INVALID_ARG;
    }
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    return err;
}
