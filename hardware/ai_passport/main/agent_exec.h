// main/agent_exec.h —— 板端命令执行器:一行协议命令 → 一串 HID 报告。纯逻辑,便于 host 测试。
//
// 协议与参考项目 esp32-iphone-agent 的 USB HID bridge 完全一致,服务器端无需修改:
//   ping | home | move <dx> <dy> | press | release | click
//   scroll <v> <h> | type <ascii text> | key <combo, e.g. cmd+h, enter>
// 每条命令回复 "ok" 或 "err <原因>"。板子不含任何决策逻辑,一切由服务器决定。
//
// 与 USB 版的区别只在传输层:报告经 BLE HID(HID over GATT)送达 iPhone。BLE 报告随
// 连接事件(约 15 ms)批量送出,所以移动按固定节拍分步发送,保持 iOS 指针加速可预测。
//
// 线程:本模块不加锁。调用方(agent_bridge)负责把所有入口串行化。
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define AGENT_MOUSE_REPORT_LEN 5   // buttons, dx, dy, wheel, pan
#define AGENT_KEY_REPORT_LEN   8   // mods, reserved, key[6]
#define AGENT_MOUSE_LEFT       0x01

// 发送 HID 报告的平台接口。mouse/keyboard 返回 false 表示报告没送出(手机断开等)。
typedef struct {
    bool (*ready)(void *ctx);
    bool (*mouse)(void *ctx, const uint8_t report[AGENT_MOUSE_REPORT_LEN]);
    bool (*keyboard)(void *ctx, const uint8_t report[AGENT_KEY_REPORT_LEN]);
    void (*sleep_ms)(void *ctx, uint32_t ms);
    void *ctx;
} agent_hid_ops_t;

typedef struct {
    int step;                // 每个报告最大合成位移(单位:鼠标计数),各方向速度一致
    uint32_t interval_ms;    // 报告间隔,恒定节拍
    int home_reports;        // home 时 (-127,-127) 报告数,足以把指针推到左上角
    uint32_t click_ms;       // click 按下到抬起
    uint32_t key_ms;         // 组合键按住时长
    int max_move;            // 单条 move 每轴上限,防止一条错误命令把执行器占用数分钟
} agent_exec_cfg_t;

#define AGENT_EXEC_CFG_DEFAULT() (agent_exec_cfg_t) { \
    .step = 10, .interval_ms = 15, .home_reports = 50, \
    .click_ms = 60, .key_ms = 30, .max_move = 10000, \
}

typedef struct {
    agent_hid_ops_t ops;
    agent_exec_cfg_t cfg;
    uint8_t buttons;   // 当前按住的鼠标键,随每个移动报告一起发送(拖动时保持按下)
    // 设备上按 OK 暂停:除 ping 外的命令都被拒绝。可在不持锁的情况下由其他任务置位,
    // 正在执行的 move/home/type 在下一个报告前看到它并中止(急停)。
    volatile bool paused;
} agent_exec_t;

void agent_exec_init(agent_exec_t *ex, const agent_hid_ops_t *ops, const agent_exec_cfg_t *cfg);

// 执行一行命令(不含换行),把回复写入 reply(总是以 '\0' 结尾)。
// 返回 true 表示回复为 "ok"。可能阻塞:move/home/type 的耗时与长度成正比。
bool agent_exec_run(agent_exec_t *ex, const char *line, char *reply, size_t reply_len);

// 松开所有鼠标键与键盘键。链路断开、暂停时调用,保证中途被打断的手势不会留下
// 长按。尽力而为:手机未连接时只清本地状态。
void agent_exec_release_all(agent_exec_t *ex);

// 暂停 / 恢复。进入暂停时会 release_all。
void agent_exec_set_paused(agent_exec_t *ex, bool paused);
