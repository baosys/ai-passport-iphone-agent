<p align="right">
  <strong>简体中文</strong> · <a href="README.md">English</a>
</p>

# AI Passport iPhone Agent

让 AI 模型以 FoloToy AI Passport 为"双手"操作一部真实的 iPhone。不需要 Mac、不需要开发者账号、不越狱,
手机上也不用安装任何东西。

本分支把 [onehumanbeing/esp32-iphone-agent](https://github.com/onehumanbeing/esp32-iphone-agent) 移植到
AI Passport(ESP32-C3)。其 agent 服务器原样使用,只有板端是新的。

## 工作原理

| 角色 | 参考项目 | 本移植 |
|---|---|---|
| 眼睛 | AirPlay 镜像到电脑上的 UxPlay | 相同 |
| 大脑 | 电脑上的规划 + 执行视觉模型(`python -m hidagent`) | 相同,无需修改 |
| 双手 | ESP32-S3 插在 iPhone 上,作为 **USB** 鼠标 + 键盘 | AI Passport 与 iPhone 配对,作为 **低功耗蓝牙(BLE)** 鼠标 + 键盘 |
| 板端链路 | Wi-Fi TCP 连接服务器 | **USB 串口**经 `tools/usb_link.py`(推荐),或 Wi-Fi TCP |

ESP32-C3 没有 USB OTG 控制器,无法充当 USB HID 设备;但它支持 BLE,而 iOS 的辅助触控(AssistiveTouch)对待
蓝牙鼠标与 USB 鼠标完全一样:指针移动、单击即轻点、拖动即滑动。手机接口因此空出来可以充电,Lightning
接口的 iPhone 也不再需要相机转接器。

```text
 iPhone ──AirPlay──▶ 电脑:UxPlay + agent 服务器(视觉模型)
   ▲                         │ 行命令:move / click / type / key ...
   │ 低功耗蓝牙 HID           ▼
   └──────────────── AI Passport ◀── USB 串口(tools/usb_link.py)或 Wi-Fi TCP
```

板子不含决策逻辑。它执行参考项目的协议(`ping`、`home`、`move <dx> <dy>`、`press`、`release`、`click`、
`scroll <v> <h>`、`type <text>`、`key <combo>`),回复 `ok` 或 `err <原因>`。移动被拆成每个报告至多 10 个单位、
每 15 ms 发送一个,让 iOS 的指针加速保持可预测,服务器的标定才能复用。

## 所需硬件

| 部件 | 说明 |
|---|---|
| AI Passport | 烧录本分支固件 |
| iPhone | 任何支持辅助触控的 iPhone;与板子之间无需线缆或转接器 |
| 电脑 | macOS 或 Linux,与手机在同一网络;运行 UxPlay 和 agent 服务器 |
| 视觉模型 | 任何支持图片输入的 OpenAI 兼容对话 API,与参考项目相同 |

## 快速开始

### 1. 编译并烧录

激活 ESP-IDF 5.5.3 后(见[环境搭建](docs/development/engineering/environment-setup.zh_CN.md)):

```bash
idf.py set-target esp32c3
idf.py build flash
```

烧录不会擦除 NVS,已有的蓝牙配对和 Wi-Fi 设置在固件升级后保留。

### 2. 启动 agent 服务器

按参考项目的[服务器安装说明](https://github.com/onehumanbeing/esp32-iphone-agent#2-install-and-start-the-server):
安装 UxPlay、克隆该项目、在 `server/.env` 写好模型 API,然后运行 `python -m hidagent`。跳过它的"烧录板子"一步。

### 3. 配对 iPhone(仅一次)

1. **设置 → 辅助功能 → 触控 → 辅助触控**:打开。
2. **设置 → 蓝牙**:点 **AI Passport** 并确认配对。Passport 屏幕显示 *Connected*,手机上出现圆形指针。

### 4. 把 Passport 连到服务器

**USB(推荐)。** 把 Passport 插到运行服务器的电脑上,启动桥接脚本:

```bash
pip install pyserial
python3 tools/usb_link.py          # 服务器在别处时加 --server HOST:PORT,加 --log 查看板端日志
```

**Wi-Fi。** 把网络和服务器的局域网地址写入 Passport 一次,之后它会自动重连,电池供电时也可以:

```bash
python3 tools/usb_link.py --wifi "My Wi-Fi" "password" --board-server 192.168.1.10:7300
```

`--show-config` 显示已保存的设置,`--clear-wifi` 回到仅 USB 模式。服务器同一时间只接受一块板子,所以 USB 链路在线时
Wi-Fi 链路处于待命,不去连接。

### 5. 镜像、标定、运行

打开 `http://<电脑 IP>:8890`,在手机上 **控制中心 → 屏幕镜像 → iPhone Agent**,在网页里执行
**Manual controls → Calibrate**,然后给 agent 一个任务。修改 `CONFIG_AGENT_MOVE_STEP`、
`CONFIG_AGENT_REPORT_INTERVAL_MS` 或辅助触控的跟踪速度后需重新标定。

## 设备上的操作

| 页面 | 内容 |
|---|---|
| **Live** | 手机(蓝牙)状态、服务器链路(USB / Wi-Fi)、最近的命令及耗时,右上角电量 |
| **Setup** | 四步设置指引与蓝牙名称 |

| 按键 | 动作 |
|---|---|
| **OK** 单击 | 暂停 / 恢复。暂停即急停:进行中的手势在下一个报告前停止,所有鼠标键与键盘键立即松开,恢复前除 `ping` 外的命令全部拒绝 |
| **UP / DOWN** 单击 | 在 Live 与 Setup 之间切换 |
| **UP** 长按(在 Setup 页) | 忘记已配对的 iPhone;重新配对前还需在手机上选择 **忽略此设备** |

30 秒内没有按键或状态变化时背光自动调暗。电量也会通过蓝牙电池服务报告给 iPhone。

## USB 串口协议

USB 串口同时输出固件日志,所以回复以 `@` 开头。除上述板端命令外,USB 链路还接受 `hello`(问候,标记链路
在线)、`bye`(链路下线,松开所有按键)、`config`、`set ssid|pass|host|port <值>`、`forget` 和 `reboot`。
`tools/usb_link.py` 使用这些命令;Wi-Fi 链路只接受板端命令。

## 配置

`idf.py menuconfig` → **AI Passport iPhone Agent**:

| 选项 | 默认值 | 含义 |
|---|---|---|
| `CONFIG_AGENT_BLE_NAME` | `AI Passport` | iPhone 蓝牙列表中显示的名称 |
| `CONFIG_AGENT_MOVE_STEP` | `10` | 每个 HID 报告的指针单位数 |
| `CONFIG_AGENT_REPORT_INTERVAL_MS` | `15` | 每个 HID 报告后的间隔 |
| `CONFIG_AGENT_HOME_REPORTS` | `50` | 把指针推到左上角所用的报告数 |
| `CONFIG_AGENT_WIFI_SSID` / `_PASSWORD` / `CONFIG_AGENT_SERVER_HOST` / `_PORT` | 空 / `7300` | 默认值,`usb_link.py --wifi` 写入的值优先 |

## 源码结构

```text
main/agent_exec.c       协议命令 -> 定速 HID 报告(纯逻辑,有 host 测试)
main/agent_keymap.c     ASCII 与组合键 -> HID 键码,美式布局(有 host 测试)
main/agent_hid.c        NimBLE + esp_hid:BLE HID 鼠标 + 键盘、绑定、广播
main/agent_bridge.c     串行执行两条链路的命令,为屏幕提供状态
main/agent_link_usb.c   USB 串口链路与本地设置命令
main/agent_link_wifi.c  Wi-Fi STA + 连接 agent 服务器的 TCP 客户端
main/agent_ui.c         Live / Setup 页面、电量、背光调暗
tools/usb_link.py       USB 串口 <-> agent 服务器桥接与设置工具
```

`components/bsp` 中的板级支持包为未修改的 FoloToy 基线。

## 局限

- 蓝牙按连接事件发送报告(11.25 到 30 ms,由 iOS 决定),指针运动比 USB 略粗;服务器的闭环瞄准会修正剩余误差。
- 走 Wi-Fi 时,Wi-Fi 与蓝牙共用一个射频,每条命令都会多一些延迟。优先使用 USB 链路。
- 键盘只能输入 ASCII,按美式布局,与参考项目相同。
- iOS 的限制同样存在:部分界面不会被镜像,手机需保持解锁。

## 负责任地使用

只自动化你拥有或被允许使用的设备和账号,并遵守相关应用的条款。让 Passport 的 OK 键或网页上的急停按钮
随时触手可及。
