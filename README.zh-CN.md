<div align="center">

# AI Passport iPhone Agent

**让 AI 操作一部真实的 iPhone：只要一个 FoloToy AI Passport，不需要开发者账号、不需要 Xcode、不用越狱。**

[English](README.md) · 简体中文

[![CI](https://github.com/lewangdev/ai-passport-iphone-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/lewangdev/ai-passport-iphone-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Board](https://img.shields.io/badge/board-AI%20Passport-red)

</div>

<p align="center"><img src="docs/images/demo-meituan.jpg" alt="示例：左边是停在美团页面的 iPhone 和显示已连接、USB 已连上的 AI Passport；右边是下单完成后的网页界面，包括实时画面、实时日志、计划、运行统计和每一步的截图" width="100%"></p>

**示例：在美团下单。** *"帮我在美团小象超市点一杯椰子水"*：Agent 输入中文“美团”找到并打开美团，进入小象超市，输入“椰子水”搜索，加入购物车，关掉凑单弹窗后提交订单并完成支付，共 28 个动作。左边是运行中的 iPhone 和 AI Passport；右边是网页界面，每一步的截图都会留在截图墙上，上面画出了点按的位置。

Agent 通过 AirPlay 屏幕镜像"看"手机，通过 AI Passport "摸"手机：AI Passport 以蓝牙鼠标加键盘的身份与 iPhone 配对。
大脑可以是任何兼容 OpenAI 接口的视觉模型，云端 API 或自己部署的模型都行。

本项目 fork 自 [onehumanbeing/esp32-iphone-agent](https://github.com/onehumanbeing/esp32-iphone-agent)。原项目的
ESP32-S3 USB 板子仍可配合同一个服务端使用，见[使用 ESP32-S3 板子](#使用-esp32-s3-板子)。

## 为什么做这个

大多数 iPhone 自动化方案通过 XCTest / WebDriverAgent 控制手机，需要 Mac、Xcode、一个签过名的辅助 App，还要 Apple
开发者账号（免费账号的签名 7 天就过期）。本项目走的是人用手机的路径：看屏幕、动指针，所以手机上什么都不用装。

|                          | AI Passport iPhone Agent | XCTest / WebDriverAgent 类工具 |
|--------------------------|--------------------------|--------------------------------|
| Apple 开发者账号         | **不需要**               | 需要（给测试程序签名）         |
| Xcode                    | **不需要**               | 需要                           |
| 电脑                     | Mac **或 Linux**         | Mac                            |
| 手机上安装 App           | **不需要**               | 需要装测试程序                 |
| 越狱                     | 不需要                   | 不需要                         |
| 额外硬件                 | AI Passport              | USB 线                         |
| 任意 App（包括游戏）     | 可以（只看像素）         | 大多可以（取决于无障碍控件树） |

## 工作原理

- **眼：AirPlay 镜像。** 手机把屏幕镜像到电脑上运行的 [UxPlay](https://github.com/FDH2/UxPlay)，GStreamer 把视频流
  转成一帧帧 JPEG，供 Agent 和网页使用。服务端让 UxPlay 一直运行，自己停止时也不关它，所以重启服务端不会中断镜像；
  镜像万一断开，板子会把它重新打开（见[保持镜像](#保持镜像)）。
- **手：蓝牙鼠标和键盘。** AI Passport 以标准蓝牙鼠标和键盘的身份与 iPhone 配对。打开 iOS 的辅助触控（AssistiveTouch）后，
  鼠标会控制屏幕上的一个指针：点击就是点按，拖动就是滑动。Passport 通过 USB（或 Wi-Fi）连接电脑，只执行简单的行命令
  （`move`、`click`、`type` 等），所有逻辑都在电脑上。
- **脑：规划模型 + 执行模型。** 规划模型根据目标和当前屏幕写一个简短的计划；执行模型每次看最新截图，选出下一个动作：
  点按、滑动、画一笔、输入、按键、回主屏幕或结束。两个角色可以用同一个模型。iOS 有键盘快捷键的地方，智能体优先按键而不是点按，
  因为按键不用移动指针、也不用瞄准：<kbd>⌘</kbd><kbd>Space</kbd> 加应用名打开应用，<kbd>⌘</kbd><kbd>H</kbd> 回主屏幕，
  输入框已经聚焦时直接输入。
- **闭环校验。** 鼠标是相对移动的，所以服务端会记录指针位置。每次点按前，它会轻推一下指针，在镜像画面里找到它的真实位置，
  偏了就先纠正，同时修正校准。每个动作之后检查屏幕是否真的变化了，并告诉模型。点空了会被发现并重试，而不是将错就错。
  同一个动作重复多次会触发重新规划；断线或模型回复出错都能自动恢复。

## 硬件

| 部件 | 说明 |
|---|---|
| **AI Passport** | FoloToy AI Passport，外加一根连接电脑的 USB 数据线。购买：[京东](https://item.jd.com/100318418103.html) · [小红书](https://www.xiaohongshu.com/goods-detail/6a8817140db08d0001d47538) · [抖音](https://v.douyin.com/5T5sJ01YpbU/) · [天猫](https://detail.tmall.com/item.htm?id=1076028511730) · [淘宝](https://item.taobao.com/item.htm?id=1075234033956)。官网：[ai-passport.folotoy.cn](https://ai-passport.folotoy.cn/) |
| **iPhone** | 任何支持辅助触控的 iPhone。手机上不插任何东西，通过蓝牙与 Passport 配对。 |
| **电脑** | macOS 或 Linux，和手机在同一个局域网；运行 UxPlay 和服务端。 |
| **视觉模型** | 任何支持图片输入、兼容 OpenAI 接口的对话模型。本地部署是可选的。 |

## 快速开始

> **让 AI 编程助手帮你装。** 在插着 Passport 的那台电脑上，把下面这段话发给 Claude Code、Codex 或类似的 Agent：
>
> ```text
> Clone https://github.com/lewangdev/ai-passport-iphone-agent and follow its README to set it up on this machine:
> flash the AI Passport plugged in over USB with the latest release, install UxPlay and the server, write
> server/.env for my model API, start the server and the USB link, and give me the web UI address.
> Ask me for the model API key.
> ```

### 1. 给 AI Passport 刷固件

从 [Releases](https://github.com/lewangdev/ai-passport-iphone-agent/releases) 下载
`ai-passport-iphone-agent-<版本>-full.bin`，把 Passport 插到电脑上并刷入（`PORT` 换成实际串口，macOS 上类似
`/dev/cu.usbmodem1101`）：

```bash
pip install esptool
python -m esptool --chip esp32c3 -p PORT -b 460800 write_flash 0x0 ai-passport-iphone-agent-<版本>-full.bin
```

如要自己编译固件，见 [`hardware/ai_passport`](hardware/ai_passport/README.zh_CN.md)。

### 2. 安装并启动服务端

```bash
# UxPlay，macOS（Homebrew 里没有 uxplay 时，从 https://github.com/FDH2/UxPlay 编译）：
brew install uxplay
# UxPlay，Debian / Ubuntu：
sudo apt install uxplay gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav

cd server
python3 -m venv .venv && .venv/bin/pip install -e . pyserial
cp .env.example .env         # 填 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
set -a && . ./.env && set +a && .venv/bin/python -m hidagent
```

浏览器打开 `http://<电脑 IP>:8890`。首次启动时 GStreamer 要扫描插件，需要几分钟。

### 3. 配对 iPhone（只需一次）

1. **设置 → 辅助功能 → 触控 → 辅助触控**：打开。
2. **设置 → 蓝牙**：在「其他设备」里点 **AI Passport** 并确认。Passport 屏幕显示 *Connected*，手机上出现圆形指针。
3. 可选：**设置 → 辅助功能 → 键盘 → 全键盘控制**：打开后，智能体可以用 <kbd>Tab</kbd> 和方向键在按钮之间移动、用
   <kbd>Space</kbd> 按下，比点按更快。

### 4. 让 Passport 连接电脑

Passport 保持插在电脑上，在另一个终端里（仓库根目录）运行：

```bash
server/.venv/bin/python hardware/ai_passport/tools/usb_link.py
```

网页显示控制器已连接。打开串口会让 Passport 重启，iPhone 约 30 秒内自动重连。如想用电池、走 Wi-Fi，见
[`hardware/ai_passport`](hardware/ai_passport/README.zh_CN.md#4-让-ai-passport-连接电脑)。

### 5. 镜像并校准

1. **控制中心 → 屏幕镜像 → iPhone Agent**，网页里出现实时画面。
2. 在网页里打开 **手动操控 → 校准**。服务端会移动指针、在实时画面里找到它，几秒内自动完成校准；
   如果找不到指针，会提示你在画面里精确点一下指针。
3. 仍在 **手动操控** 里，点 **学习镜像点击位置**，之后镜像断开时就能自动恢复（见[保持镜像](#保持镜像)）。

### 6. 下达任务

输入一个目标（例如"打开设置，进入 Wi-Fi"），点 **开始**；也可以直接点下面的示例。

### 使用 ESP32-S3 板子

原项目的板子（插在手机 USB 口上、充当 USB 鼠标和键盘的 ESP32-S3）也能配合同一个服务端使用。实测使用
[Seeed Studio XIAO ESP32S3](https://wiki.seeedstudio.com/cn/xiao_esp32s3_getting_started/)；任何 USB-C 口直连 S3 原生 USB
的 ESP32-S3 板子都可以。Lightning 机型需要 Lightning 转 USB 相机转换器。安装
[arduino-cli](https://arduino.github.io/arduino-cli/) 和 ESP32 core（3.x），然后：

```bash
cp hardware/hid_bridge/secrets.example.h hardware/hid_bridge/secrets.h
# 编辑 secrets.h：填 2.4 GHz Wi-Fi 名称和密码，以及运行服务端的电脑的 IP
hardware/flash.sh            # 可选参数：[串口] [--debug]
```

用它时把板子插到手机上，代替第 3、4 步；板子通过 Wi-Fi 连接服务端。

## 网页界面

- **实时手机画面**：点击 = 点按，拖动 = 滑动，滚轮 = 滚动；也可以手动回主屏幕、返回、输入文字和按键。
- **实时日志**：每一个计划、思考和动作实时显示，带耗时。
- **步骤截图墙**：每一步模型做决定时看到的截图，上面画出点按或滑动的位置，附带模型的理由和思考、执行、等画面稳定各用了多久；
  点卡片可以放大。
- **点按**用红色圆圈和扩散的波纹标出，滑动和笔画用黄色轨迹；**动效**开关可以关掉动画。每次点按前都会在画面上找到指针并修正位置。
- **紧急停止**（或按 <kbd>Esc</kbd>）：立即停止 Agent 并松开指针。**清空记录**：清空日志和截图墙。
- **自动重连镜像**：学习"屏幕镜像"的点击位置、打开或关闭自动重连、立即重连。
- 界面支持英文和简体中文。

## 保持镜像

**服务端重启时。** UxPlay 运行在独立的会话里，服务端退出后它仍在运行：停止服务端（Ctrl+C、关掉终端）后手机继续镜像，
下次启动时直接接管正在运行的接收端（记录在 `~/.hidagent/receiver.json`）。UxPlay 自己退出时，服务端会重新启动它。
它的输出写到 `~/.hidagent/uxplay.log`（上一次的在 `uxplay.log.1`）。要彻底关掉它：`pkill uxplay`。
修改 `AIRPLAY_NAME` 后，下次启动会替换掉旧的接收端。

**镜像断开时**（在手机上关掉了、别的设备占用了接收端、网络抖动），服务端会用板子把它重新打开：从屏幕边缘下滑打开控制中心，
再点"屏幕镜像"和接收端。镜像断开时没有画面可看，所以这几下点击要在镜像开着时学习一次：**手动操控 → 学习镜像点击位置**
会打开控制中心和接收端列表，让视觉模型找到每个点击位置，然后关掉它们。位置保存在 `~/.hidagent/mirror.json`，之后盲点回放；
手机重新连上接收端就说明成功了。

- 断开后先等 5 秒，然后最多尝试四次（立即、15 秒、45 秒、2 分钟后），之后放弃，直到镜像恢复或把 **自动重连镜像** 关掉再打开。
- Agent 执行任务时绝不重连，重连进行中也不能开始任务。紧急停止会取消正在进行的重连，并像放弃一样暂停自动重连。
- 手机仍连着接收端时绝不点接收端（那样会停止镜像）。
- 手机画面不动时不发送画面，所以刚重启后服务端会把指针挪动一像素来要一帧。如果手机连着却 30 秒都没有画面，会重启接收端。
- 移动了控制中心里的"屏幕镜像"或改了接收端名字后，请重新学习。

API：`GET /api/mirror`（状态和学到的点击位置），`POST /api/mirror`，内容为 `{"auto": true|false}`，
`POST /api/mirror/learn`，`POST /api/mirror/reconnect`。`/api/status` 的 `mirror` 字段里也有状态。

## 配置

写在 `server/.env` 里（参考 [`.env.example`](server/.env.example)）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:8000/v1` | 兼容 OpenAI 的接口地址 |
| `LLM_MODEL` / `LLM_API_KEY` | — | 模型名称和 API Key |
| `LLM_EXTRA_BODY` | `{}` | 合并进每次请求的 JSON（例如关闭模型的思考模式） |
| `LLM_TOKEN_PARAM` | `auto` | 输出长度参数名：`max_tokens`、`max_completion_tokens`（新版 OpenAI 模型），或 `auto` 自动识别 |
| `ACTOR_*` / `PLANNER_*` | — | 单独覆盖执行模型 / 规划模型的任意 `LLM_*` 配置 |
| `TYPESAFE_API_KEY` | — | 可选。让 [Jev](https://typesafe.ai) 判断哪些步骤不用调用模型、把换了说法的任务对上记住的做法、找到换了位置的按钮、确认重放的步骤在对的页面上、看任务是否已经完成 |
| `JEV_MODEL` / `JEV_VISION_MODEL` | `jev-latest` / `jev-preview` | Jev 处理文字和截图用的模型 |
| `AIRPLAY_NAME` | `iPhone Agent` | 手机"屏幕镜像"列表里显示的名字 |
| `PORT` / `DEVICE_PORT` | `8890` / `7300` | 网页端口 / 板子连接的端口 |
| `HIDAGENT_DATA` | `~/.hidagent` | 校准数据和调试记录 |
| `HIDAGENT_DEBUG` | `0` | 调试模式，见下文 |
| `HIDAGENT_POINTER_LAG` | `0.35` | 轻推指针后等待多少秒再在镜像画面里找它（蓝牙会增加延迟） |
| `HIDAGENT_MAX_STEPS` | `100` | 每个任务最多执行多少步，超过就放弃；`0` 表示不限，一直做到完成、失败或你手动停止 |
| `HIDAGENT_PASTE` | 有 `pbcopy`（macOS）时开启 | 设为 `0` 关闭通过通用剪贴板粘贴中文，改用拼音输入 |

### 本地模型（可选）

用云端 API 就够了。如果想全部跑在自己的硬件上，下面是本项目开发和测速时用的配置：

- 模型：[nvidia/Qwen3.8-Flash-Next-NVFP4](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4)
- 部署：[Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold)，
  两台 NVIDIA DGX Spark 张量并行（TP=2），提供 OpenAI 兼容接口
- 速度：关闭思考模式后，每个动作约 1.3 秒，每次规划约 2 秒：

```bash
LLM_BASE_URL=http://<dgx-spark-ip>:8888/v1
LLM_MODEL=Qwen3.8-Flash-Next
LLM_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}}'
```

其他兼容 OpenAI 接口的服务（vLLM、SGLang、llama.cpp、Ollama 等）配上视觉模型，用法完全一样。

## 调试模式

设置 `HIDAGENT_DEBUG=1` 后，每条板子命令及其耗时都会写进日志，每次 Agent 运行会单独保存一个文件夹：

```
~/.hidagent/traces/20261012-005207/
├── steps.jsonl   # 每次模型调用（提示词、原始回复、耗时）和每个动作
├── 002.jpg       # 每次模型调用看到的截图
└── ...
```

`hardware/ai_passport/tools/usb_link.py --log` 会在转发的命令旁显示 AI Passport 的日志。ESP32-S3 板子则用
`hardware/flash.sh --debug` 编译调试版固件，通过 USB 串口回显每条命令和耗时。

## 板子协议

板子本身不含任何逻辑。连上时先发送 `hello <上次重启原因> <开机毫秒数>`，之后服务端通过 TCP 每行发一条命令，板子回复 `ok` 或 `err <原因>`。AI Passport 走 USB 时，由 `usb_link.py` 替它建立这条 TCP 连接。

| 命令 | 作用 |
|---|---|
| `ping` | 健康检查 |
| `home` | 把指针推到左上角（一个已知位置） |
| `move <dx> <dy>` | 相对移动指针，匀速移动，让 iOS 的指针加速度可预测 |
| `press` / `release` / `click` | 左键按下 / 松开 / 点击 |
| `scroll <v> <h>` | 滚轮 |
| `type <text>` | 输入 ASCII 文字 |
| `key <combo>` | 按键，例如 `enter`、`cmd+h`、`cmd+space` |

与服务端的连接断开时，板子会松开所有按键并自动重连。iPhone 未配对或超出蓝牙范围时，AI Passport 回复 `err phone not connected`；按 Passport 的 OK 键可暂停所有命令，作为急停。ESP32-S3 板子在手机锁屏、USB 休眠时回复 `err phone usb asleep`，没有响应时由看门狗自动重启。

## 目录结构

```
hardware/   ai_passport/：AI Passport固件，蓝牙 HID（ESP-IDF），以及 USB 连接工具
            hid_bridge/ 和 flash.sh：原项目的 ESP32-S3 USB HID 固件（Arduino）
server/     Agent、网页界面、屏幕采集和板子连接（Python，FastAPI）
            serve/：可选的极简服务，用来自己部署 Hugging Face 视觉模型
```

## 路线图

- [ ] 测试基于 DiffusionGemma 的纯本地方案（`server/serve/` 里有实验性的 OpenAI 兼容服务）
- [ ] 支持横屏 App（目前坐标和校准都按竖屏计算）
- [x] 中文输入（通用剪贴板粘贴，拼音输入兜底）
- [ ] 更多常用 App 的示例任务

## 限制

- iOS 不镜像部分界面（输入密码、Apple Pay、有版权保护的视频），Agent 看不到这些画面。
- 手机锁屏后镜像会断开，Agent 工作时请关闭自动锁定。
- 板子只能输入 ASCII。中文通过通用剪贴板粘贴（macOS；Mac 和 iPhone 需登录同一 Apple 账号并打开“接力”），
  粘贴没有生效时改为输入拼音，这需要 iPhone 当前使用简体拼音键盘，并且总是选第一个候选词。通用剪贴板关闭时，
  手机会粘贴它自己剪贴板里的旧内容，这种情况请设置 `HIDAGENT_PASTE=0`。表情等没有对应按键的字符会被跳过。
- 修改辅助触控的跟踪速度后需要重新校准。
- 暂不支持横屏 App：在横屏界面里的点按和绘图位置会偏。
- 在有多个网卡的 Linux 机器上，如果手机列表里看不到接收端，把 Avahi（mDNS）限制在手机所在网络的网卡上。

## 合理使用

只自动化你自己拥有或被允许使用的设备和账号，并遵守相关 App 的条款。Agent 是在替你动手：请看着它，紧急停止按钮随时备好。

## 参与贡献

欢迎提 Issue 和 Pull Request。提交前请先跑一遍检查：

```bash
cd server && pip install -e ".[dev]" && ruff check . && pytest
```

AI Passport 固件请在 `hardware/ai_passport` 中运行 `tools/validate.sh`（见其
[编译与测试说明](hardware/ai_passport/docs/development/engineering/build-and-test.zh_CN.md)）。

## 相关项目

其他用代码或 AI 操作 iPhone 的方案，以及与本项目的区别。如有错误欢迎指正。

| 项目 | 做法 | 手机 / 主机需要 | 与本项目的区别 |
|---|---|---|---|
| [VersaiOS](https://github.com/zhaodashuaibi/VersaiOS) | UxPlay 镜像 + ESP32-S3 蓝牙鼠标 + 视觉模型 | ESP32 通过 USB 串口连电脑；桌面 GUI，主要是 Windows | 思路最接近。一条指令点一次，没有规划步骤，也不比对前后画面 |
| [ja1chen/actell（执言）](https://github.com/ja1chen/actell) | iPhone 上装 agent App，通过苹果 CoreDevice 调试通道截屏和点按 | 开发者模式、配对 + Developer Disk Image、Xcode 自签 | 本项目不需要开发者模式，不用在手机上装 App，也不用签名 |
| [AidenAI-IO/aiden-firmware](https://github.com/AidenAI-IO/aiden-firmware) | 专用板：HDMI 采集 + USB 键鼠 + 多模态模型 | HDMI 采集芯片和扩展坞；以 Android 为主 | 本项目用 AirPlay 代替 HDMI 采集，不需要采集硬件 |
| [yoyicue/glassbox](https://github.com/yoyicue/glassbox) | PicoKVM HDMI 采集 + USB HID，观察/决策/执行/验证循环 | macOS 控制端和 PicoKVM | 本项目在 Linux 上运行，只要一块小板子 |
| [boboabw/phone-agent](https://github.com/boboabw/phone-agent) | ESP32-S3 蓝牙 HID + 辅助触控，agent 是 iPhone App | 需要 Mac + Xcode 编译签名 | 本项目手机上什么都不用装 |
| [dovydaskel/Iphone-control-for-social-media](https://github.com/dovydaskel/Iphone-control-for-social-media) | ESP32 蓝牙鼠标 + OpenCV 指针校正，脚本化发帖 | Mac | 指针闭环思路相近；本项目多了视觉模型的规划/执行 |
| [VKCOM/devicehub](https://github.com/VKCOM/devicehub) | ESP32 蓝牙鼠标，用于 iOS 设备农场 | 设备农场基础设施 | 设备农场工具，没有 AI agent |
| [NolanCotter/AirBridge](https://github.com/NolanCotter/AirBridge) / [gbulog/pcairplay](https://github.com/gbulog/pcairplay) | Linux 上的 UxPlay + 电脑自带蓝牙模拟键鼠 | 带蓝牙的 Linux 电脑 | 人工远程操作，没有 AI |

其他路线：基于 WebDriverAgent/XCUITest 的工具（如 [mobile-mcp](https://github.com/mobile-next/mobile-mcp)、[Midscene](https://github.com/web-infra-dev/midscene)）需要 Mac 和代码签名；基于 macOS iPhone 镜像的工具需要 Mac。

## 致谢

[onehumanbeing/esp32-iphone-agent](https://github.com/onehumanbeing/esp32-iphone-agent)（原项目） ·
[UxPlay](https://github.com/FDH2/UxPlay)（AirPlay 接收端） ·
[FoloToy AI Passport BSP](https://github.com/FoloToy/ai-passport) 和 [ESP-IDF](https://github.com/espressif/esp-idf) / NimBLE（蓝牙 HID） ·
[Arduino ESP32 core](https://github.com/espressif/arduino-esp32) 和 [TinyUSB](https://github.com/hathach/tinyusb)（USB HID） ·
[FastAPI](https://fastapi.tiangolo.com/)

## 许可证

[MIT](LICENSE)
