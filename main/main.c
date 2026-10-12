// main/main.c —— AI Passport iPhone Agent:让 AI 模型通过蓝牙鼠标/键盘操作一部真实 iPhone。
//
// 参考 https://github.com/onehumanbeing/esp32-iphone-agent 的架构:
//   眼睛:iPhone 通过 AirPlay 把屏幕镜像到电脑(UxPlay),服务器取帧
//   大脑:服务器上的视觉模型规划并逐步决定动作
//   双手:本设备。参考项目是插在 iPhone USB 口上的 ESP32-S3 USB HID;
//        AI Passport(ESP32-C3)改为 BLE HID,iPhone 通过蓝牙配对使用
// 板子不含决策逻辑,只执行服务器发来的一行行命令(agent_exec.h)。
//
// 按键:
//   OK   单击   暂停 / 恢复(急停:立即松开按键并拒绝后续命令)
//   UP/DOWN 单击 在 Live / Setup 视图间切换
//   UP   长按   忘记已配对的 iPhone(仅 Setup 视图,防误触)
#include "agent_bridge.h"
#include "agent_config.h"
#include "agent_hid.h"
#include "agent_link.h"
#include "agent_ui.h"

#include "bsp_battery.h"
#include "bsp_button.h"
#include "bsp_display.h"
#include "bsp_i2c.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "lvgl.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

static const char *TAG = "main";

#define BATTERY_PERIOD_MS (30 * 1000)

typedef struct {
    bsp_btn_t btn;
    bsp_btn_ev_t ev;
} key_event_t;

static QueueHandle_t s_keys;
static bool s_on_setup;

// 按键回调运行在共享 esp_timer 任务:只入队,不做任何慢操作。
static void on_key(bsp_btn_t btn, bsp_btn_ev_t ev, void *user)
{
    (void)user;
    if (ev != BSP_BTN_CLICK && ev != BSP_BTN_LONG) return;
    const key_event_t k = { .btn = btn, .ev = ev };
    (void)xQueueSend(s_keys, &k, 0);
}

static void handle_key(const key_event_t *k)
{
    agent_ui_wake();
    if (k->btn == BSP_BTN_OK && k->ev == BSP_BTN_CLICK) {
        // 可能等待当前报告发完(几十毫秒),所以放在按键任务而非回调里。
        agent_bridge_toggle_pause();
    } else if ((k->btn == BSP_BTN_UP || k->btn == BSP_BTN_DOWN) && k->ev == BSP_BTN_CLICK) {
        if (bsp_lvgl_lock(500)) {
            agent_ui_show_next();
            bsp_lvgl_unlock();
            s_on_setup = !s_on_setup;
        }
    } else if (k->btn == BSP_BTN_UP && k->ev == BSP_BTN_LONG && s_on_setup) {
        ESP_LOGI(TAG, "forgetting paired phone");
        agent_hid_forget();
    }
}

static void key_task(void *arg)
{
    (void)arg;
    key_event_t k;
    for (;;) {
        if (xQueueReceive(s_keys, &k, portMAX_DELAY) == pdTRUE) handle_key(&k);
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "AI Passport iPhone Agent");

    // 绑定密钥和配置都存在 NVS。不自动擦除:那会丢掉已配对的手机。
    esp_err_t err = nvs_flash_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "NVS init failed: %s (not erased automatically)", esp_err_to_name(err));
    }

    bsp_i2c_init();
    if (bsp_display_init() != ESP_OK || !bsp_lvgl_init()) {
        ESP_LOGE(TAG, "display init failed; continuing headless");
    } else {
        bsp_display_backlight(100);
    }
    bool battery_ok = bsp_battery_init() == ESP_OK;

    agent_config_t cfg;
    agent_config_load(&cfg);
    agent_bridge_init();

    // 先起 USB 链路:即使 BLE 或 Wi-Fi 失败,也能通过串口查看日志、修改配置。
    err = agent_link_usb_start();
    if (err != ESP_OK) ESP_LOGE(TAG, "USB link: %s", esp_err_to_name(err));
    err = agent_hid_start(CONFIG_AGENT_BLE_NAME);
    if (err != ESP_OK) ESP_LOGE(TAG, "BLE HID: %s", esp_err_to_name(err));
    err = agent_link_wifi_start(&cfg);
    if (err != ESP_OK) ESP_LOGE(TAG, "Wi-Fi link: %s", esp_err_to_name(err));

    if (bsp_lvgl_lock(1000)) {
        agent_ui_start(CONFIG_AGENT_BLE_NAME, cfg.ssid[0] != '\0');
        bsp_lvgl_unlock();
    }

    s_keys = xQueueCreate(8, sizeof(key_event_t));
    if (s_keys && xTaskCreate(key_task, "keys", 3072, NULL, 5, NULL) == pdPASS) {
        err = bsp_button_init(on_key, NULL);
        if (err != ESP_OK) ESP_LOGE(TAG, "buttons: %s", esp_err_to_name(err));
    }

    // 无 PSRAM:Wi-Fi + NimBLE + LVGL 同时运行后的余量,上机验证时看这一行。
    ESP_LOGI(TAG, "ready: free heap %u, largest block %u",
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));

    // 电量:屏幕右上角显示,同时通过 BLE 电池服务报给 iPhone。
    for (;;) {
        int soc = battery_ok ? bsp_battery_soc() : -1;
        agent_ui_set_battery(soc);
        if (soc >= 0) agent_hid_set_battery((uint8_t)soc);
        vTaskDelay(pdMS_TO_TICKS(BATTERY_PERIOD_MS));
    }
}
