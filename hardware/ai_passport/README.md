<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# AI Passport iPhone Agent

Let an AI model use a real iPhone, with a FoloToy AI Passport as its hands. The Passport pairs with the iPhone over
Bluetooth as a mouse + keyboard; the agent server on your computer watches the phone through AirPlay screen
mirroring and tells the Passport where to tap and what to type. Nothing is installed on the phone.

## What you need

- AI Passport and a USB data cable. Buy: [JD.com](https://item.jd.com/100318418103.html) · [Xiaohongshu](https://www.xiaohongshu.com/goods-detail/6a8817140db08d0001d47538) · [Douyin](https://v.douyin.com/5T5sJ01YpbU/) · [Tmall](https://detail.tmall.com/item.htm?id=1076028511730) · [Taobao](https://item.taobao.com/item.htm?id=1075234033956)
  (official site: [ai-passport.folotoy.cn](https://ai-passport.folotoy.cn/en/))
- An iPhone
- A computer (macOS or Linux) on the same Wi-Fi as the iPhone
- An API key for an OpenAI-compatible model that accepts images

## Get it running

### 1. Flash the firmware to the AI Passport

Download `ai-passport-iphone-agent-<version>-full.bin` from
[Releases](https://github.com/lewangdev/ai-passport-iphone-agent/releases), plug the Passport into the computer, and
flash it (replace `PORT`, e.g. `/dev/cu.usbmodem1101` on macOS):

```bash
pip install esptool
python -m esptool --chip esp32c3 -p PORT -b 460800 write_flash 0x0 ai-passport-iphone-agent-<version>-full.bin
```

To build from source instead, activate ESP-IDF 5.5.3 (see [environment setup](docs/development/engineering/environment-setup.md))
and run `idf.py set-target esp32c3 && idf.py build flash` in this directory.

### 2. Run the server on the computer

From the repository root:

```bash
brew install uxplay            # macOS; on Debian/Ubuntu: sudo apt install uxplay gstreamer1.0-plugins-{good,bad} gstreamer1.0-libav
cd server
python3 -m venv .venv && .venv/bin/pip install -e . pyserial
cp .env.example .env           # set LLM_BASE_URL, LLM_MODEL and LLM_API_KEY
set -a && . ./.env && set +a && .venv/bin/python -m hidagent
```

If Homebrew has no `uxplay`, build it from [FDH2/UxPlay](https://github.com/FDH2/UxPlay) and put it on your `PATH`.
The first start takes a few minutes while GStreamer scans its plugins.

### 3. Pair AI Passport with the iPhone (once)

1. **Settings → Accessibility → Touch → AssistiveTouch**: turn it on.
2. **Settings → Bluetooth**: tap **AI Passport** under *Other Devices* and confirm. The Passport screen shows
   *Connected* and a round pointer appears on the phone.

### 4. Connect AI Passport to the computer

Plug the Passport into the computer running the server and leave this running in a second terminal, from the
repository root:

```bash
server/.venv/bin/python hardware/ai_passport/tools/usb_link.py
```

It prints `linked: ...` once the server has the board. Opening the port restarts the Passport; the iPhone reconnects
on its own within about 30 seconds.

To run on battery over Wi-Fi instead, store the network and this computer's LAN address on the Passport once:
`usb_link.py --wifi "My Wi-Fi" "password" --board-server 192.168.1.10:7300` (`--clear-wifi` undoes it).

### 5. Mirror the screen and give it a task

1. Open `http://<computer-ip>:8890`.
2. On the iPhone: **Control Center → Screen Mirroring → iPhone Agent**.
3. In the web page, run **Manual controls → Calibrate**, then type a task, e.g. *"Open Settings"*.

Re-calibrate after changing the AssistiveTouch tracking speed, `CONFIG_AGENT_MOVE_STEP` or
`CONFIG_AGENT_REPORT_INTERVAL_MS`.

## On the device

| Screen | Content |
|---|---|
| **Live** | Phone (Bluetooth) state, server link (USB / Wi-Fi), the latest commands with their duration, battery in the top-right corner |
| **Setup** | The four setup steps and the Bluetooth name |

| Button | Action |
|---|---|
| **OK** click | Pause / resume. Pausing is an emergency stop: a gesture in progress stops before its next report, every button and key is released, and all commands except `ping` are refused until resumed |
| **UP / DOWN** click | Switch between Live and Setup |
| **UP** hold (on Setup) | Forget the paired iPhone; also choose **Forget This Device** on the phone before pairing again |

The backlight dims after 30 seconds without a button press or a state change. The battery level is also reported
to the iPhone through the Bluetooth battery service.

## USB serial protocol

The USB serial port also carries the firmware log, so replies start with `@`. Besides the board commands above,
the USB link accepts `hello` (greeting, marks the link up), `bye` (link down, releases everything), `config`,
`set ssid|pass|host|port <value>`, `forget` and `reboot`. `tools/usb_link.py` uses these; the Wi-Fi link only
accepts the board commands.

## Configuration

`idf.py menuconfig` → **AI Passport iPhone Agent**:

| Option | Default | Meaning |
|---|---|---|
| `CONFIG_AGENT_BLE_NAME` | `AI Passport` | Name in the iPhone's Bluetooth list |
| `CONFIG_AGENT_MOVE_STEP` | `10` | Pointer units per HID report |
| `CONFIG_AGENT_REPORT_INTERVAL_MS` | `15` | Pause after each HID report |
| `CONFIG_AGENT_HOME_REPORTS` | `50` | Reports used to push the pointer into the top-left corner |
| `CONFIG_AGENT_WIFI_SSID` / `_PASSWORD` / `CONFIG_AGENT_SERVER_HOST` / `_PORT` | empty / `7300` | Defaults until `usb_link.py --wifi` stores other values |

## Source layout

```text
main/agent_exec.c       protocol commands -> paced HID reports (pure logic, host-tested)
main/agent_keymap.c     ASCII and key combos -> HID key codes, US layout (host-tested)
main/agent_hid.c        NimBLE + esp_hid: BLE HID mouse + keyboard, bonding, advertising
main/agent_bridge.c     serializes commands from both links, status for the screen
main/agent_link_usb.c   USB serial link and local settings commands
main/agent_link_wifi.c  Wi-Fi station + TCP client to the agent server
main/agent_ui.c         Live / Setup screens, battery, backlight dimming
tools/usb_link.py       USB serial <-> agent server bridge and settings tool
```

The board support package in `components/bsp` is the unchanged FoloToy baseline.

## Limitations

- Bluetooth delivers reports once per connection event (11.25 to 30 ms, chosen by iOS), so pointer motion is
  slightly coarser than over USB; the server's closed-loop aiming corrects the remaining error.
- Over Wi-Fi, Wi-Fi and Bluetooth share one radio, which adds latency to each command. Prefer the USB link.
- Keyboard input is ASCII with the US layout, as in the reference project. The Passport screen shows Chinese
  (all of GB2312) in commands and Wi-Fi names, but `type` cannot enter Chinese on the phone.
- The same iOS limits apply: some screens are not mirrored, and the phone must stay unlocked.

## Responsible use

Only automate devices and accounts you own or are allowed to use, and respect the terms of the apps involved.
Keep the Passport's OK button, or the web UI's emergency stop, within reach.
