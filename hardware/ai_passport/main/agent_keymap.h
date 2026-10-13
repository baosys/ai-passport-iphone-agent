// main/agent_keymap.h —— ASCII / 组合键 → USB HID 键码(美式布局)。纯逻辑,便于 host 测试。
//
// iPhone 把蓝牙键盘当作外接键盘处理,键码按 HID Usage Page 0x07 解释,布局按
// iOS「硬件键盘」设置(默认美式)。因此这里只覆盖美式布局下可直接敲出的 ASCII。
#pragma once

#include <stdbool.h>
#include <stdint.h>

// 修饰键位(键盘输入报告第 0 字节)。
#define AGENT_MOD_CTRL  0x01
#define AGENT_MOD_SHIFT 0x02
#define AGENT_MOD_ALT   0x04
#define AGENT_MOD_GUI   0x08   // iOS 上即 Command

// 一个组合键最多同时按下的普通键数量(键盘输入报告的 6 个键位)。
#define AGENT_KEYS_MAX 6

typedef struct {
    uint8_t mods;
    uint8_t keys[AGENT_KEYS_MAX];
    uint8_t key_count;
} agent_combo_t;

// 单个 ASCII 字符 → 键码与所需修饰键。'\n' 视为回车,'\t' 视为 Tab。
// 不支持的字符(非 ASCII、控制字符)返回 false。
bool agent_keymap_ascii(char c, uint8_t *key, uint8_t *mods);

// 解析 "cmd+h"、"enter"、"cmd+shift+3"、"cmd+space" 这样的组合键。
// 名称与参考项目(USB HID bridge)一致:enter esc tab backspace space up down left right
// cmd ctrl alt shift;额外支持 delete。单个字符按 agent_keymap_ascii() 映射。
// 名称未知、出现空段或普通键超过 AGENT_KEYS_MAX 时返回 false。
bool agent_keymap_combo(const char *text, agent_combo_t *out);
