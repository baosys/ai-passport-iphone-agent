<p align="right">
  <strong>简体中文</strong> · <a href="README.md">English</a>
</p>

# AI Passport iPhone Agent

让 AI 模型以 FoloToy AI Passport 为"双手"操作一部真实的 iPhone。Passport 通过蓝牙与 iPhone 配对,充当鼠标 + 键盘;
电脑上的 agent 服务端通过 AirPlay 屏幕镜像看手机,告诉 Passport 点哪里、输入什么。手机上不用安装任何东西。

## 准备

- AI Passport 和一根 USB 数据线。购买：[京东](https://item.jd.com/100318418103.html) · [小红书](https://www.xiaohongshu.com/goods-detail/6a8817140db08d0001d47538) · [抖音](https://v.douyin.com/5T5sJ01YpbU/) · [天猫](https://detail.tmall.com/item.htm?id=1076028511730) · [淘宝](https://item.taobao.com/item.htm?id=1075234033956)
  (官网:[ai-passport.folotoy.cn](https://ai-passport.folotoy.cn/))
- 一部 iPhone
- 一台电脑(macOS 或 Linux),与 iPhone 连同一个 Wi-Fi
- 一个支持图片输入、兼容 OpenAI 接口的模型 API Key

## 跑起来

### 1. 把固件刷到 AI Passport

从 [Releases](https://github.com/lewangdev/ai-passport-iphone-agent/releases) 下载
`ai-passport-iphone-agent-<版本>-full.bin`,把 Passport 插到电脑上,然后刷入(`PORT` 换成实际串口,macOS 上
类似 `/dev/cu.usbmodem1101`):

```bash
pip install esptool
python -m esptool --chip esp32c3 -p PORT -b 460800 write_flash 0x0 ai-passport-iphone-agent-<版本>-full.bin
```

如要从源码编译:激活 ESP-IDF 5.5.3(见[环境搭建](docs/development/engineering/environment-setup.zh_CN.md)),
在本目录执行 `idf.py set-target esp32c3 && idf.py build flash`。

### 2. 在电脑上运行服务端

在仓库根目录执行:

```bash
brew install uxplay            # macOS;Debian/Ubuntu:sudo apt install uxplay gstreamer1.0-plugins-{good,bad} gstreamer1.0-libav
cd server
python3 -m venv .venv && .venv/bin/pip install -e . pyserial
cp .env.example .env           # 填写 LLM_BASE_URL、LLM_MODEL 和 LLM_API_KEY
set -a && . ./.env && set +a && .venv/bin/python -m hidagent
```

如果 Homebrew 里没有 `uxplay`,从 [FDH2/UxPlay](https://github.com/FDH2/UxPlay) 编译并放进 `PATH`。首次启动时
GStreamer 要扫描插件,需要几分钟。

### 3. 在 iPhone 上配对 AI Passport(仅一次)

1. **设置 → 辅助功能 → 触控 → 辅助触控**:打开。
2. **设置 → 蓝牙**:在「其他设备」里点 **AI Passport** 并确认。Passport 屏幕显示 *Connected*,手机上出现圆形指针。

### 4. 让 AI Passport 连接电脑

把 Passport 插到运行服务端的电脑上,在另一个终端里(仓库根目录)运行并保持:

```bash
server/.venv/bin/python hardware/ai_passport/tools/usb_link.py
```

服务端接上板子后会打印 `linked: ...`。打开串口会让 Passport 重启,iPhone 约 30 秒内自动重连。

如想用电池、走 Wi-Fi,把网络和这台电脑的局域网地址写入 Passport 一次即可:
`usb_link.py --wifi "My Wi-Fi" "password" --board-server 192.168.1.10:7300`(`--clear-wifi` 可撤销)。

### 5. 镜像屏幕,下达任务

1. 打开 `http://<电脑 IP>:8890`。
2. 在 iPhone 上:**控制中心 → 屏幕镜像 → iPhone Agent**。
3. 在网页里执行 **Manual controls → Calibrate**,然后输入任务,例如 *"打开设置"*。

修改辅助触控的跟踪速度、`CONFIG_AGENT_MOVE_STEP` 或 `CONFIG_AGENT_REPORT_INTERVAL_MS` 后需重新标定。

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
- 键盘只能输入 ASCII,按美式布局,与参考项目相同。Passport 屏幕能显示命令和 Wi-Fi 名中的中文(GB2312 全部
  字符),但 `type` 无法在手机上输入中文。
- iOS 的限制同样存在:部分界面不会被镜像,手机需保持解锁。

## 负责任地使用

只自动化你拥有或被允许使用的设备和账号,并遵守相关应用的条款。让 Passport 的 OK 键或网页上的急停按钮
随时触手可及。
