// main/agent_hid.h —— BLE HID 组合设备(鼠标 + 键盘),iPhone 通过蓝牙配对后当作外接指针使用。
//
// 为什么是 BLE 而不是 USB:参考项目用 ESP32-S3 的原生 USB OTG 把板子模拟成 USB 鼠标;
// AI Passport 的 ESP32-C3 只有 USB-Serial-JTAG,不能当 USB HID 设备,但支持 BLE。
// iOS 的 AssistiveTouch 对蓝牙鼠标与 USB 鼠标一视同仁:指针、点击、拖动都一样。
//
// 绑定信息存 NVS(CONFIG_BT_NIMBLE_NVS_PERSIST),配对一次后 iPhone 会自动回连。
#pragma once

#include "esp_err.h"
#include <stdbool.h>
#include <stdint.h>

typedef enum {
    AGENT_HID_OFF = 0,     // 未启动或启动失败
    AGENT_HID_ADVERTISING, // 等待 iPhone 连接(首次需在 iPhone 蓝牙设置中配对)
    AGENT_HID_CONNECTED,   // 已连接,链路尚未加密(配对 / 恢复绑定中)
    AGENT_HID_READY,       // 已加密,可以发送报告
} agent_hid_state_t;

// 初始化 NimBLE 与 HID 服务并开始广播。须在 nvs_flash_init() 之后调用一次。
esp_err_t agent_hid_start(const char *name);

agent_hid_state_t agent_hid_state(void);

// 发送一个输入报告(长度见 agent_exec.h)。手机未就绪或链路断开时返回 false。
// 发送缓冲暂满时会短暂重试;可在任意任务调用,但同一时刻只应由一个任务发送
// (agent_bridge 的互斥锁保证)。
bool agent_hid_mouse(const uint8_t *report);
bool agent_hid_keyboard(const uint8_t *report);

// 删除所有绑定、断开当前连接并重新广播。iPhone 端还需「忽略此设备」。
esp_err_t agent_hid_forget(void);

// 更新 BLE 电池服务里的电量(iPhone 的电池小组件会显示)。
void agent_hid_set_battery(uint8_t percent);
