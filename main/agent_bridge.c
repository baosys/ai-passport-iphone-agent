#include "agent_bridge.h"

#include "agent_exec.h"
#include "agent_hid.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "sdkconfig.h"

static const char *TAG = "agent_bridge";

static agent_exec_t s_exec;
static SemaphoreHandle_t s_exec_lock;   // 一条命令执行期间持有
static SemaphoreHandle_t s_state_lock;  // 只保护 s_snap,持有时间极短
static agent_snapshot_t s_snap;

static bool ops_ready(void *ctx)
{
    (void)ctx;
    return agent_hid_state() == AGENT_HID_READY;
}

static bool ops_mouse(void *ctx, const uint8_t r[AGENT_MOUSE_REPORT_LEN])
{
    (void)ctx;
    return agent_hid_mouse(r);
}

static bool ops_keyboard(void *ctx, const uint8_t r[AGENT_KEY_REPORT_LEN])
{
    (void)ctx;
    return agent_hid_keyboard(r);
}

static void ops_sleep(void *ctx, uint32_t ms)
{
    (void)ctx;
    vTaskDelay(pdMS_TO_TICKS(ms));
}

void agent_bridge_init(void)
{
    s_exec_lock = xSemaphoreCreateMutex();
    s_state_lock = xSemaphoreCreateMutex();
    configASSERT(s_exec_lock && s_state_lock);
    const agent_hid_ops_t ops = {
        .ready = ops_ready, .mouse = ops_mouse, .keyboard = ops_keyboard,
        .sleep_ms = ops_sleep, .ctx = NULL,
    };
    agent_exec_cfg_t cfg = AGENT_EXEC_CFG_DEFAULT();
    cfg.step = CONFIG_AGENT_MOVE_STEP;
    cfg.interval_ms = CONFIG_AGENT_REPORT_INTERVAL_MS;
    cfg.home_reports = CONFIG_AGENT_HOME_REPORTS;
    agent_exec_init(&s_exec, &ops, &cfg);
}

static void record(const char *line, uint32_t ms, bool ok)
{
    xSemaphoreTake(s_state_lock, portMAX_DELAY);
    memmove(&s_snap.recent[1], &s_snap.recent[0], sizeof(s_snap.recent[0]) * (AGENT_RECENT_COUNT - 1));
    agent_recent_t *r = &s_snap.recent[0];
    snprintf(r->line, sizeof(r->line), "%s", line);
    r->ms = ms > UINT16_MAX ? UINT16_MAX : (uint16_t)ms;
    r->ok = ok;
    s_snap.commands++;
    if (!ok) s_snap.errors++;
    xSemaphoreGive(s_state_lock);
}

void agent_bridge_handle(agent_link_t from, const char *line, char *reply, size_t reply_len)
{
    (void)from;
    int64_t started = esp_timer_get_time();
    xSemaphoreTake(s_exec_lock, portMAX_DELAY);
    bool ok = agent_exec_run(&s_exec, line, reply, reply_len);
    xSemaphoreGive(s_exec_lock);
    if (strcmp(line, "ping") == 0) return;  // 服务器每 2 s 一次心跳,不计入活动
    uint32_t ms = (uint32_t)((esp_timer_get_time() - started) / 1000);
    record(line, ms, ok);
    ESP_LOGD(TAG, "%s -> %s (%lu ms)", line, reply, (unsigned long)ms);
}

static const char *reset_reason(void)
{
    switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "power-on";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_TASK_WDT: case ESP_RST_INT_WDT: case ESP_RST_WDT: return "watchdog";
    case ESP_RST_PANIC: return "crash";
    case ESP_RST_SW: return "software";
    case ESP_RST_USB: return "usb";
    default: return "other";
    }
}

void agent_bridge_hello(char *out, size_t len)
{
    snprintf(out, len, "hello %s %lu", reset_reason(),
             (unsigned long)(esp_timer_get_time() / 1000));
}

void agent_bridge_link_lost(agent_link_t from)
{
    (void)from;
    xSemaphoreTake(s_exec_lock, portMAX_DELAY);
    agent_exec_release_all(&s_exec);
    xSemaphoreGive(s_exec_lock);
}

void agent_bridge_set_link(agent_link_t link, agent_link_state_t state, const char *detail)
{
    xSemaphoreTake(s_state_lock, portMAX_DELAY);
    bool changed = s_snap.link[link] != state;
    s_snap.link[link] = state;
    if (link == AGENT_LINK_WIFI && detail) {
        changed |= strcmp(s_snap.wifi_detail, detail) != 0;
        snprintf(s_snap.wifi_detail, sizeof(s_snap.wifi_detail), "%s", detail);
    }
    if (changed) s_snap.change_seq++;
    xSemaphoreGive(s_state_lock);
}

agent_link_state_t agent_bridge_link_state(agent_link_t link)
{
    xSemaphoreTake(s_state_lock, portMAX_DELAY);
    agent_link_state_t state = s_snap.link[link];
    xSemaphoreGive(s_state_lock);
    return state;
}

bool agent_bridge_toggle_pause(void)
{
    // 暂停是急停:先不持锁置位,让正在执行的 move/home/type 在下一个报告前中止,
    // 再拿锁松开所有按键。
    bool paused = !s_exec.paused;
    if (paused) s_exec.paused = true;
    xSemaphoreTake(s_exec_lock, portMAX_DELAY);
    agent_exec_set_paused(&s_exec, paused);
    xSemaphoreGive(s_exec_lock);

    xSemaphoreTake(s_state_lock, portMAX_DELAY);
    s_snap.paused = paused;
    s_snap.change_seq++;
    xSemaphoreGive(s_state_lock);
    ESP_LOGI(TAG, "%s", paused ? "paused" : "resumed");
    return paused;
}

void agent_bridge_snapshot(agent_snapshot_t *out)
{
    xSemaphoreTake(s_state_lock, portMAX_DELAY);
    *out = s_snap;
    xSemaphoreGive(s_state_lock);
}
