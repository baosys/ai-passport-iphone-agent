// main/agent_ui.h —— 设备屏幕:桥接器状态面板 + 首次使用指引。
//
// 两个视图,UP/DOWN 单击切换:
//   Live  —— 手机(BLE)状态、服务器链路、最近执行的命令、暂停横幅
//   Setup —— 首次使用的四步指引
// UI 只从 agent_bridge_snapshot() / agent_hid_state() 轮询状态,不被其他任务直接调用,
// 因此除 agent_ui_start() 与 agent_ui_show_next() 外无需持有 LVGL 锁。
#pragma once

#include <stdbool.h>

// 创建界面与刷新定时器。调用方须持有 bsp_lvgl_lock()。
void agent_ui_start(const char *ble_name, bool wifi_configured);

// 切换 Live / Setup 视图。调用方须持有 bsp_lvgl_lock()。
void agent_ui_show_next(void);

// 点亮屏幕并重新计时;按键、状态变化时调用。任意任务可调用。
void agent_ui_wake(void);

// 最新电量(0..100,-1 = 不可用)。任意任务可调用。
void agent_ui_set_battery(int percent);
