<div align="center">

# AI Passport iPhone Agent

**Let an AI model use a real iPhone with a FoloToy AI Passport. No Mac, no developer account, no jailbreak.**

English · [简体中文](README.zh-CN.md)

[![CI](https://github.com/lewangdev/ai-passport-iphone-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/lewangdev/ai-passport-iphone-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Board](https://img.shields.io/badge/board-AI%20Passport-red)

</div>

<p align="center"><img src="docs/images/demo-meituan.jpg" alt="Example: left, the iPhone in Meituan next to the AI Passport showing Connected and USB linked; right, the web UI after the order, with the live screen, live log, plan, run summary and step screenshots" width="100%"></p>

**Example — order on Meituan.** *"帮我在美团小象超市点一杯椰子水"* (order a coconut water from Meituan's Xiaoxiang Supermarket): the agent types "美团" to find and open Meituan, opens Xiaoxiang Supermarket, searches for "椰子水" (both typed in Chinese), adds it to the cart, closes the add-on popup, places the order and pays, in 28 actions. Left: the iPhone and the AI Passport during the run; right: the web UI, where every step's screenshot, with its tap drawn on top, stays on the step wall.

The agent sees the phone through AirPlay screen mirroring and touches it through an AI Passport, which pairs with
the iPhone over Bluetooth as a mouse and keyboard. Any OpenAI-compatible vision model can be the brain — a cloud
API, or a model you run yourself.

This project is a fork of [onehumanbeing/esp32-iphone-agent](https://github.com/onehumanbeing/esp32-iphone-agent).
Its original ESP32-S3 USB board still works with the same server; see [Using the ESP32-S3 board](#using-the-esp32-s3-board).

## Why

Most iPhone automation drives the phone through XCTest / WebDriverAgent. That needs a Mac, Xcode, a signed
helper app and an Apple developer account (a free account's signature expires every 7 days). This project
uses the same paths a person uses — the screen and a pointer — so the phone needs nothing installed.

|                                  | AI Passport iPhone Agent  | XCTest / WebDriverAgent tools |
|----------------------------------|---------------------------|-------------------------------|
| Apple developer account          | **Not needed**            | Needed (to sign the runner)   |
| Xcode                            | **Not needed**            | Needed                        |
| Computer                         | Mac **or Linux**          | Mac                           |
| App installed on the phone       | **None**                  | Test runner app               |
| Jailbreak                        | Not needed                | Not needed                    |
| Extra hardware                   | AI Passport               | USB cable                     |
| Works in any app, incl. games    | Yes (it only sees pixels) | Mostly (depends on the accessibility tree) |

## How it works

- **Eyes — AirPlay mirroring.** The phone mirrors its screen to [UxPlay](https://github.com/FDH2/UxPlay) running
  on your computer; a GStreamer pipeline turns the stream into JPEG frames for the agent and the web page.
  The server keeps UxPlay running and leaves it running when it stops, so a restart does not end the mirroring;
  when mirroring drops anyway, the board turns it back on (see [Keeping mirroring on](#keeping-mirroring-on)).
- **Hands — a Bluetooth mouse and keyboard.** The AI Passport pairs with the iPhone as a standard Bluetooth mouse and
  keyboard. With iOS AssistiveTouch on, the mouse moves an on-screen pointer, so a click is a tap and a drag is a
  swipe. The Passport connects to the computer over USB (or Wi-Fi) and executes simple line commands (`move`,
  `click`, `type`, …); all logic stays on the computer.
- **Brain — a planner and an actor.** A planner model writes a short plan from the goal and the current screen.
  An actor model then looks at each new screenshot and picks the next action: tap, swipe, draw a path, type, press
  a key, go home, or finish. Both roles can be the same model. Where iOS has a keyboard shortcut, the agent uses it
  instead of a tap, since a key press needs no pointer travel or aiming: <kbd>⌘</kbd><kbd>Space</kbd> and the app
  name to open an app, <kbd>⌘</kbd><kbd>H</kbd> for the home screen, typing straight into a focused field.
- **Closing the loop.** The pointer is relative, so the server tracks where it is. Before each tap it wiggles the
  pointer, finds it on the mirrored screen and corrects any miss, refining the calibration as it goes. After each
  action it checks whether the screen actually changed and tells the model, so a missed tap is noticed and retried
  instead of built upon. Repeated actions trigger a re-plan; a dropped link or a bad reply is recovered from.

## Hardware

| Part | Notes |
|---|---|
| **AI Passport** | FoloToy AI Passport, plus a USB data cable to the computer. Buy: [JD.com](https://item.jd.com/100318418103.html) · [Xiaohongshu](https://www.xiaohongshu.com/goods-detail/6a8817140db08d0001d47538) · [Douyin](https://v.douyin.com/5T5sJ01YpbU/) · [Tmall](https://detail.tmall.com/item.htm?id=1076028511730) · [Taobao](https://item.taobao.com/item.htm?id=1075234033956). Official site: [ai-passport.folotoy.cn](https://ai-passport.folotoy.cn/en/) |
| **iPhone** | Any iPhone with AssistiveTouch. Nothing is plugged into the phone; it pairs with the Passport over Bluetooth. |
| **Computer** | macOS or Linux, on the same network as the phone; runs UxPlay and the server. |
| **Vision model** | Any OpenAI-compatible chat API that accepts images. Running a model locally is optional. |

## Quick start

> **Set up with an AI coding agent.** Paste this into Claude Code, Codex or a similar agent on the computer the
> Passport is plugged into:
>
> ```text
> Clone https://github.com/lewangdev/ai-passport-iphone-agent and follow its README to set it up on this machine:
> flash the AI Passport plugged in over USB with the latest release, install UxPlay and the server, write
> server/.env for my model API, start the server and the USB link, and give me the web UI address.
> Ask me for the model API key.
> ```

### 1. Flash the AI Passport

Download `ai-passport-iphone-agent-<version>-full.bin` from
[Releases](https://github.com/lewangdev/ai-passport-iphone-agent/releases), plug the Passport into the computer and
flash it (replace `PORT`, e.g. `/dev/cu.usbmodem1101` on macOS):

```bash
pip install esptool
python -m esptool --chip esp32c3 -p PORT -b 460800 write_flash 0x0 ai-passport-iphone-agent-<version>-full.bin
```

To build the firmware yourself, see [`hardware/ai_passport`](hardware/ai_passport/README.md).

### 2. Install and start the server

```bash
# UxPlay — macOS (if Homebrew has no uxplay, build it from https://github.com/FDH2/UxPlay):
brew install uxplay
# UxPlay — Debian / Ubuntu:
sudo apt install uxplay gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav

cd server
python3 -m venv .venv && .venv/bin/pip install -e . pyserial
cp .env.example .env         # set LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
set -a && . ./.env && set +a && .venv/bin/python -m hidagent
```

Open `http://<computer-ip>:8890` in a browser. The first start takes a few minutes while GStreamer scans its plugins.

### 3. Pair the iPhone (once)

1. **Settings → Accessibility → Touch → AssistiveTouch**: turn it on.
2. **Settings → Bluetooth**: tap **AI Passport** under *Other Devices* and confirm. The Passport screen shows
   *Connected* and a round pointer appears on the phone.
3. Optional: **Settings → Accessibility → Keyboards → Full Keyboard Access**: turn it on to let the agent move
   between buttons with <kbd>Tab</kbd> and the arrow keys and press them with <kbd>Space</kbd>, which is faster than
   tapping.

### 4. Connect the Passport to the computer

Keep the Passport plugged into the computer and run this in a second terminal, from the repository root:

```bash
server/.venv/bin/python hardware/ai_passport/tools/usb_link.py
```

The web page shows *controller connected*. Opening the port restarts the Passport; the iPhone reconnects on its own
within about 30 seconds. To run the Passport on battery over Wi-Fi instead, see
[`hardware/ai_passport`](hardware/ai_passport/README.md#4-connect-ai-passport-to-the-computer).

### 5. Mirror and calibrate

1. **Control Center → Screen Mirroring → iPhone Agent**. The live screen appears in the web page.
2. In the web page, open **Manual controls → Calibrate**. The server moves the pointer, finds it on the live screen
   and calibrates itself in a few seconds; if it cannot see the pointer, it asks you to click exactly on it.
3. Still in **Manual controls**, press **Learn mirroring taps** so mirroring can come back by itself when it drops
   (see [Keeping mirroring on](#keeping-mirroring-on)).

### 6. Give it a task

Type a goal (for example *"Open Settings and go to Wi-Fi"*) and press **Start**, or click one of the examples.

### Using the ESP32-S3 board

The original board from the upstream project, an ESP32-S3 plugged into the phone's USB port as a USB mouse and
keyboard, works with the same server. Tested with a
[Seeed Studio XIAO ESP32S3](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/); any ESP32-S3 board whose
USB-C port is wired to the S3's native USB works. Lightning iPhones need a Lightning to USB Camera Adapter.
Install [arduino-cli](https://arduino.github.io/arduino-cli/) and the ESP32 core (3.x), then:

```bash
cp hardware/hid_bridge/secrets.example.h hardware/hid_bridge/secrets.h
# edit secrets.h: 2.4 GHz Wi-Fi name/password and the IP address of the computer running the server
hardware/flash.sh            # optional arguments: [serial port] [--debug]
```

Plug the board into the phone instead of steps 3 and 4; it connects to the server over Wi-Fi.

## Web UI

- **Live phone** — click to tap, drag to swipe, scroll to scroll; manual Home, Back, typing and keys.
- **Live log** — every plan, thought and action as it happens, with timings.
- **Step wall** — the screenshot the model decided on for every step, with its tap or swipe drawn on top, plus the
  model's reasoning and how long thinking, acting and waiting for the screen to settle took. Click a card to zoom in.
- **Taps** are marked with a red ring and a spreading ripple, swipes and strokes with a yellow path; the **Motion** switch
  turns the animation off. Every tap finds the pointer on screen and corrects it before pressing.
- **Emergency stop** (or <kbd>Esc</kbd>) stops the agent and releases the pointer. **Clear history** empties the log.
- **Reconnect mirroring** — learn the Screen Mirroring taps, turn automatic reconnecting on or off, or reconnect now.
- English and Simplified Chinese interface.

## Keeping mirroring on

**Across server restarts.** UxPlay runs in its own session and outlives the server: stopping the server (Ctrl+C,
closing its terminal) leaves the phone mirroring, and the next start reuses the running receiver, noted in
`~/.hidagent/receiver.json`. If UxPlay exits by itself, the server starts it again. Its output goes to
`~/.hidagent/uxplay.log` (the previous run's to `uxplay.log.1`). To stop it for good: `pkill uxplay`. A changed
`AIRPLAY_NAME` replaces the old receiver on the next start.

**When mirroring drops** (turned off on the phone, another device took the receiver, a network hiccup), the server turns it
back on with the board: an edge swipe opens Control Center, then it taps Screen Mirroring and the receiver. With
mirroring off there is nothing to look at, so the taps are learned once while mirroring is on: **Manual controls →
Learn mirroring taps** opens Control Center and the receiver list, lets the vision model find each tap and closes
them again. The taps are saved in `~/.hidagent/mirror.json` and replayed blind; the phone connecting to the receiver
again is the check that it worked.

- It waits 5 seconds after a drop, then tries up to four times (now, after 15 s, 45 s and 2 min) and gives up until
  mirroring is back or **Reconnect mirroring** is switched off and on.
- It never runs during an agent task, and an agent task cannot start while it runs. The emergency stop cancels a
  reconnect and pauses reconnecting the same way as giving up.
- It never taps the receiver while the phone is still connected (that would stop mirroring).
- The phone sends no frames while its screen is still, so right after a restart the server nudges the pointer by a
  pixel to get one. If the phone stays connected with no video for 30 seconds, the receiver is restarted.
- Learn again after moving the Screen Mirroring control or renaming the receiver.

API: `GET /api/mirror` (state and learned taps), `POST /api/mirror` with `{"auto": true|false}`,
`POST /api/mirror/learn`, `POST /api/mirror/reconnect`. `/api/status` includes the state under `mirror`.

## Configuration

Set in `server/.env` (see [`.env.example`](server/.env.example)):

| Variable | Default | Meaning |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:8000/v1` | OpenAI-compatible endpoint |
| `LLM_MODEL` / `LLM_API_KEY` | — | Model name and API key |
| `LLM_EXTRA_BODY` | `{}` | JSON merged into every request (e.g. to turn off a model's thinking mode) |
| `LLM_TOKEN_PARAM` | `auto` | Token-limit field: `max_tokens`, `max_completion_tokens` (newer OpenAI models), or `auto` to detect |
| `ACTOR_*` / `PLANNER_*` | — | Per-role overrides of any `LLM_*` variable |
| `TYPESAFE_API_KEY` | — | Optional. Lets [Jev](https://typesafe.ai) decide which steps need no model, match reworded tasks to remembered ones, find a remembered button that moved, check that a replayed step is on the right page, and see whether a task looks done |
| `JEV_MODEL` / `JEV_VISION_MODEL` | `jev-latest` / `jev-preview` | Jev models for text and for screenshots |
| `AIRPLAY_NAME` | `iPhone Agent` | Receiver name in the phone's Screen Mirroring list |
| `PORT` / `DEVICE_PORT` | `8890` / `7300` | Web UI port / port the board connects to |
| `HIDAGENT_DATA` | `~/.hidagent` | Calibration and debug traces |
| `HIDAGENT_DEBUG` | `0` | Debug mode, see below |
| `HIDAGENT_POINTER_LAG` | `0.35` | Seconds to wait after nudging the pointer before looking for it on the mirrored screen (Bluetooth adds latency) |
| `HIDAGENT_MAX_STEPS` | `100` | Most actions per task before the agent gives up; `0` runs until the task is done, fails, or you stop it |
| `HIDAGENT_PASTE` | on when the Mac is signed in to iCloud | `1` / `0` forces pasting Chinese through Universal Clipboard on / off |
| `HIDAGENT_RELAY_HOST` | the address the phone mirrors to | Address Safari opens for the Chinese text relay |
| `HIDAGENT_SHORTCUT_BUTTON` | `3` | Mouse button that runs the paste Shortcut; `0` turns it off |
| `HIDAGENT_SECRET_<NAME>` | — | A password the agent may enter as `{NAME}`, see [Passwords](#passwords) |
| `HIDAGENT_SECRET_<NAME>_MAX` | — | The largest amount a task using `{NAME}` may state |
| `HIDAGENT_KEYCHAIN_SECRETS` | — | Comma-separated Keychain names to load at start, so they are hidden even when typed into a task |

### Passwords

Never type a password into a task: the models, the traces and the routines would all see it. Store it on the Mac
instead and name it in the task as `{NAME}`:

```bash
security add-generic-password -s hidagent -a WECHAT_PAY -w   # preferred: the macOS Keychain, read per task
HIDAGENT_SECRET_WECHAT_PAY=123456          # or in server/.env (used when the Keychain has no such item)
HIDAGENT_SECRET_WECHAT_PAY_MAX=200         # optional cap on the amount a task using it may state
```

A task such as *"Send Alice ¥1 on WeChat and pay with {WECHAT_PAY}"* keeps the placeholder all the way through. The
actor answers `secret WECHAT_PAY` to type it into the focused field, or `keypad WECHAT_PAY X1 Y1 X2 Y2` for a digit
keypad an app draws itself (WeChat Pay's): it gives only roughly where the keypad is. The server then finds the 3×4
keys on the screen itself (digits, the blank key, delete), clears the field, presses each digit and waits for its
dot. If a dot does not appear, or too many do, it clears the field and tries once more; a second miss, or the app
leaving the sheet half-way, ends the task with *"Gave up"* instead of trying again, since wrong payment passwords
lock the account. Nobody has to be at the phone: the actor picks *Use Password* over Face ID.

Each secret is entered at most once per task, only if the task names it, and no command or tap of it is logged;
every use writes one `secret` line with its name and outcome (never the value) to the trace. With `_MAX` set, a task
must state its amount (`¥20`, `20元`, `20 yuan`) and is refused before it starts when any amount is above the cap.
The cap is checked against the task text, not read off the payment screen. A task that needs a password with nothing
stored stops and hands over (*"Your turn"*) so you enter it on the phone. Any stored value that turns up in text is
replaced by its placeholder before it reaches a model, a trace or a log, and tasks with secrets are never remembered
as routines.

### Local model (optional)

A cloud API is enough. If you want everything on your own hardware, this is the setup the project was developed
and timed with:

- Model: [nvidia/Qwen3.8-Flash-Next-NVFP4](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4)
- Serving: [Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Dual-DGX-Sparks-TensorFold)
  — two NVIDIA DGX Sparks with tensor parallelism (TP=2), OpenAI-compatible API
- Speed: about 1.3 s per action and 2 s per plan, with thinking mode off:

```bash
LLM_BASE_URL=http://<dgx-spark-ip>:8888/v1
LLM_MODEL=Qwen3.8-Flash-Next
LLM_EXTRA_BODY='{"chat_template_kwargs": {"enable_thinking": false}}'
```

Any other OpenAI-compatible server (vLLM, SGLang, llama.cpp, Ollama, …) with a vision model works the same way.

## Debug mode

Set `HIDAGENT_DEBUG=1` to log every board command with its latency and to save one folder per agent run:

```
~/.hidagent/traces/20261012-005207/
├── steps.jsonl   # every model call (prompt, raw reply, latency) and every action
├── 002.jpg       # the screenshot each model call saw
└── ...
```

`hardware/ai_passport/tools/usb_link.py --log` shows the AI Passport's log next to the relayed commands. For the
ESP32-S3 board, `hardware/flash.sh --debug` builds firmware that echoes each command and its duration over USB serial.

## Board protocol

The board holds no logic. On connecting it sends `hello <last reset reason> <uptime ms>`; then the server sends one
command per line over TCP and the board answers `ok` or `err <reason>`. With the AI Passport on USB,
`usb_link.py` makes that TCP connection for it.

| Command | Effect |
|---|---|
| `ping` | Health check |
| `home` | Push the pointer into the top-left corner (a known position) |
| `move <dx> <dy>` | Relative pointer move, at a constant pace so iOS pointer acceleration stays predictable |
| `press` / `release` / `click` | Left button down / up / both |
| `button <n>` | Click mouse button 1–3 (3 is the middle button; AssistiveTouch can map it to a Shortcut) |
| `scroll <v> <h>` | Scroll wheel |
| `wheel <v> <ms>` | Smooth scroll: v wheel units over about ms, eased in and out (falls back to `scroll`) |
| `type <text>` | Type ASCII text |
| `key <combo>` | Press keys, e.g. `enter`, `cmd+h`, `cmd+space` |

If the link to the server drops, the board releases all buttons and reconnects on its own. The AI Passport answers
`err phone not connected` while the iPhone is not paired or out of Bluetooth range, and its OK button pauses every
command as an emergency stop. The ESP32-S3 board answers `err phone usb asleep` while the locked phone's USB port
sleeps, and a watchdog restarts it if it ever stops responding.

## Project layout

```
hardware/   ai_passport/: AI Passport firmware, Bluetooth HID (ESP-IDF), and the USB link tool
            hid_bridge/ and flash.sh: original ESP32-S3 USB HID firmware (Arduino)
server/     agent, web UI, screen capture and board link (Python, FastAPI)
            serve/: optional minimal server to host a Hugging Face vision model yourself
```

## Roadmap

- [ ] Test a fully local setup with DiffusionGemma (`server/serve/` has an experimental OpenAI-compatible server)
- [ ] Landscape apps (screen coordinates and calibration assume portrait)
- [x] Exact Chinese text input (Universal Clipboard, a paste Shortcut or a Safari relay, pinyin as the last resort)
- [ ] More example tasks across popular apps

## Limitations

- iOS does not mirror some screens (passcode entry, Apple Pay, DRM video); the agent cannot see them.
- Mirroring stops when the phone locks; keep auto-lock off while the agent works.
- The board types key codes, not characters, so Chinese is pasted, exactly and at any length. With the Mac signed
  in to the phone's Apple ID (Handoff on), Universal Clipboard carries it in about a second. Without it, a paste
  Shortcut does the same in about two seconds without leaving the app (set up below). Otherwise the server
  offers the text on a one-time page: the phone opens it in Safari, one tap copies it, a swipe along the bottom edge
  returns to the app and the text is pasted (about 20 seconds; it replaces the phone's clipboard). Pinyin typing,
  which picks the first candidate, is only the last resort.

  Paste Shortcut, once per phone (needs the AI Passport firmware with the `button` command): in Shortcuts, make a
  Shortcut "AI Passport Paste" with **Get Contents of URL** `http://<Mac name>.local:8890/clip` (the Mac name is
  `scutil --get LocalHostName`) and **Copy to Clipboard**, run it once and allow the connection. Then Settings →
  Accessibility → Touch → AssistiveTouch → Devices → AI Passport → Customize Additional Buttons: while iOS waits
  for a button, send `button 3` from the server (`curl -X POST localhost:8890/api/raw -H 'content-type:
  application/json' -d '{"text":"button 3"}'` with `HIDAGENT_DEBUG=1`) and assign the Shortcut.
  `HIDAGENT_SHORTCUT_BUTTON` picks another button; `0` turns the Shortcut off.
- Re-calibrate after changing AssistiveTouch tracking speed.
- Landscape apps are not supported yet: drawings and taps there land in the wrong place.
- On Linux machines with several network interfaces, limit Avahi (mDNS) to the interface on the phone's network
  if the phone does not list the receiver.

## Responsible use

Only automate devices and accounts you own or are allowed to use, and respect the terms of the apps involved. The
agent acts with your hands: watch it, and keep the emergency stop within reach.

## Contributing

Issues and pull requests are welcome. Run the checks before sending a change:

```bash
cd server && pip install -e ".[dev]" && ruff check . && pytest
```

For the AI Passport firmware, run `tools/validate.sh` in `hardware/ai_passport` (see its
[build and test guide](hardware/ai_passport/docs/development/engineering/build-and-test.md)).

## Related projects

Other ways people drive an iPhone with code or AI, and how this project differs. Corrections welcome.

| Project | Approach | Needs on the phone / host | Difference from this project |
|---|---|---|---|
| [VersaiOS](https://github.com/zhaodashuaibi/VersaiOS) | UxPlay mirroring + ESP32-S3 Bluetooth mouse + vision model | ESP32 tethered to a computer over USB serial; desktop GUI, mainly Windows | Closest in spirit. One tap per instruction, no planning step or before/after screen check |
| [ja1chen/actell](https://github.com/ja1chen/actell) | Agent app on the iPhone, screenshots and taps via Apple's CoreDevice debug channel | Developer Mode, pairing + Developer Disk Image, Xcode self-signing | This project needs no Developer Mode, no app on the phone and no signing |
| [AidenAI-IO/aiden-firmware](https://github.com/AidenAI-IO/aiden-firmware) | Dedicated board: HDMI capture + USB keyboard/mouse + multimodal model | HDMI capture chip and dock; mainly Android | Uses AirPlay instead of HDMI capture, so no capture hardware |
| [yoyicue/glassbox](https://github.com/yoyicue/glassbox) | PicoKVM HDMI capture + USB HID, observe/decide/act/verify loop | macOS controller and PicoKVM | Runs on Linux with a single small board |
| [boboabw/phone-agent](https://github.com/boboabw/phone-agent) | ESP32-S3 Bluetooth HID + AssistiveTouch, agent runs as an iPhone app | Mac + Xcode to build and sign the app | Nothing to install on the phone |
| [dovydaskel/Iphone-control-for-social-media](https://github.com/dovydaskel/Iphone-control-for-social-media) | ESP32 Bluetooth mouse with OpenCV pointer correction, scripted posting | Mac | Similar closed-loop pointer idea; this project adds a vision-model planner/executor |
| [VKCOM/devicehub](https://github.com/VKCOM/devicehub) | ESP32 Bluetooth mouse for iOS device farms | Farm infrastructure | Device-farm tooling, no AI agent |
| [NolanCotter/AirBridge](https://github.com/NolanCotter/AirBridge) / [gbulog/pcairplay](https://github.com/gbulog/pcairplay) | UxPlay on Linux + the PC's own Bluetooth as keyboard/mouse | Linux PC with Bluetooth | Manual remote control, no AI |

Other routes: WebDriverAgent/XCUITest-based tools such as [mobile-mcp](https://github.com/mobile-next/mobile-mcp) and [Midscene](https://github.com/web-infra-dev/midscene) need a Mac and code signing; tools built on macOS iPhone Mirroring need a Mac.

## Acknowledgements

[onehumanbeing/esp32-iphone-agent](https://github.com/onehumanbeing/esp32-iphone-agent) (the original project) ·
[UxPlay](https://github.com/FDH2/UxPlay) (AirPlay receiver) ·
[FoloToy AI Passport BSP](https://github.com/FoloToy/ai-passport) and [ESP-IDF](https://github.com/espressif/esp-idf) / NimBLE (Bluetooth HID) ·
[Arduino ESP32 core](https://github.com/espressif/arduino-esp32) and [TinyUSB](https://github.com/hathach/tinyusb) (USB HID) ·
[FastAPI](https://fastapi.tiangolo.com/)

## License

[MIT](LICENSE)
