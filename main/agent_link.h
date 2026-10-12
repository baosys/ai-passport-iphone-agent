// main/agent_link.h —— 服务器链路。两条链路共用同一套文本协议(见 agent_exec.h):
//
//   USB:AI Passport 插在运行服务器的电脑上,tools/usb_link.py 把 USB 串口桥接到
//        服务器的 TCP 端口(默认 7300)。免配置、延迟低,推荐。
//   Wi-Fi:与参考固件相同,板子主动连接 <host>:<port>。需先用 usb_link.py 写入
//        Wi-Fi 与服务器地址;未配置时不启动。
//
// 服务器同一时间只接受一块板子:USB 链路在线时 Wi-Fi 链路待命。命令由 agent_bridge 串行执行。
#pragma once

#include "agent_config.h"
#include "esp_err.h"

// USB-Serial-JTAG 同时是日志控制台。协议回复以 '@' 开头,桥接脚本据此把它们和
// 日志分开;发往板子的命令照常一行一条。
esp_err_t agent_link_usb_start(void);

esp_err_t agent_link_wifi_start(const agent_config_t *cfg);
