// main/agent_bridge.h —— 把各链路(USB / Wi-Fi)收到的命令串行化后交给执行器,
// 并记录供屏幕显示的状态。
//
// 线程:所有函数可在任意任务调用。执行锁在一条命令执行期间持有(move 可能持续
// 数秒);状态快照用另一把短锁,UI 读取时不会被长命令阻塞。
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum {
    AGENT_LINK_USB = 0,
    AGENT_LINK_WIFI,
    AGENT_LINK_COUNT,
} agent_link_t;

typedef enum {
    AGENT_LINK_OFF = 0,     // 未启用(Wi-Fi 未配置)
    AGENT_LINK_WAITING,     // 等待:Wi-Fi 连接中 / 等服务器 / 等 USB 桥接脚本
    AGENT_LINK_UP,          // 服务器在线
} agent_link_state_t;

#define AGENT_RECENT_COUNT 5

typedef struct {
    char line[40];          // 命令(按 UTF-8 字符边界截断)
    uint16_t ms;            // 执行耗时
    bool ok;
} agent_recent_t;

typedef struct {
    agent_link_state_t link[AGENT_LINK_COUNT];
    char wifi_detail[96];   // 例如 "192.168.1.23 > 192.168.1.10:7300"
    bool paused;
    uint32_t commands;      // 自启动以来执行的命令数(不含 ping)
    uint32_t errors;
    agent_recent_t recent[AGENT_RECENT_COUNT];  // [0] 最新
    uint32_t change_seq;    // 链路或暂停状态每变一次 +1,UI 据此点亮屏幕
} agent_snapshot_t;

void agent_bridge_init(void);

// 执行一行命令,回复写入 reply。由各链路的接收任务调用。
void agent_bridge_handle(agent_link_t from, const char *line, char *reply, size_t reply_len);

// 连接服务器后的第一行:"hello <上次复位原因> <运行毫秒>",与参考固件相同。
void agent_bridge_hello(char *out, size_t len);

// 链路断开时调用:松开所有按键,避免被截断的拖动变成长按。
void agent_bridge_link_lost(agent_link_t from);

void agent_bridge_set_link(agent_link_t link, agent_link_state_t state, const char *detail);

agent_link_state_t agent_bridge_link_state(agent_link_t link);

bool agent_bridge_toggle_pause(void);

void agent_bridge_snapshot(agent_snapshot_t *out);
