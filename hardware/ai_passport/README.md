<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# AI Passport iPhone Agent

Let an AI model use a real iPhone with a FoloToy AI Passport as its hands. No Mac, no developer account, no
jailbreak, and nothing installed on the phone.

This directory ports [onehumanbeing/esp32-iphone-agent](https://github.com/onehumanbeing/esp32-iphone-agent) to the
AI Passport (ESP32-C3). It replaces the ESP32-S3 board in [`../hid_bridge`](../hid_bridge); the agent server in
[`../../server`](../../server) is the same for both boards. Commands below run from this directory.

## How it works

| Role | Reference project | This port |
|---|---|---|
| Eyes | AirPlay mirroring to UxPlay on the computer | Same |
| Brain | Planner + actor vision models on the computer (`python -m hidagent`) | Same, unchanged |
| Hands | ESP32-S3 plugged into the iPhone as a **USB** mouse + keyboard | AI Passport paired to the iPhone as a **Bluetooth LE** mouse + keyboard |
| Board link | Wi-Fi TCP to the server | **USB serial** through `tools/usb_link.py` (recommended) or Wi-Fi TCP |

The ESP32-C3 has no USB OTG controller, so it cannot be a USB HID device. It does have Bluetooth LE, and iOS
AssistiveTouch treats a Bluetooth mouse exactly like a USB one: the pointer moves, a click is a tap, a drag is a
swipe. The phone's port stays free for charging, and Lightning iPhones need no camera adapter.

```text
 iPhone ──AirPlay──▶ computer: UxPlay + agent server (vision model)
   ▲                         │ line commands: move / click / type / key ...
   │ Bluetooth LE HID        ▼
   └──────────────── AI Passport ◀── USB serial (tools/usb_link.py) or Wi-Fi TCP
```

The board holds no logic. It executes the reference protocol (`ping`, `home`, `move <dx> <dy>`, `press`,
`release`, `click`, `scroll <v> <h>`, `type <text>`, `key <combo>`) and answers `ok` or `err <reason>`. Moves are
split into reports of at most 10 units sent every 15 ms, so iOS pointer acceleration stays predictable and the
server's calibration holds.

## What you need

| Part | Notes |
|---|---|
| AI Passport | Flashed with this firmware |
| iPhone | Any iPhone with AssistiveTouch; no cable or adapter to the board |
| Computer | macOS or Linux on the same network as the phone; runs UxPlay and the agent server |
| Vision model | Any OpenAI-compatible chat API that accepts images, as in the reference project |

## Quick start

### 1. Build and flash

With ESP-IDF 5.5.3 activated (see [environment setup](docs/development/engineering/environment-setup.md)):

```bash
idf.py set-target esp32c3
idf.py build flash
```

Flashing keeps NVS, so an existing Bluetooth pairing and Wi-Fi settings survive firmware updates.

### 2. Start the agent server

Follow the [server setup](../../README.md#2-install-and-start-the-server) in the repository README: install UxPlay,
write `server/.env` with your model API, and run `python -m hidagent`. Skip its "Flash the board" step, which is for
the ESP32-S3.

### 3. Pair the iPhone (once)

1. **Settings → Accessibility → Touch → AssistiveTouch**: turn it on.
2. **Settings → Bluetooth**: tap **AI Passport** and confirm pairing. The Passport screen shows *Connected* and a
   round pointer appears on the phone.

### 4. Link the Passport to the server

**USB (recommended).** Plug the Passport into the computer running the server and start the bridge:

```bash
pip install pyserial
python3 tools/usb_link.py          # add --server HOST:PORT if the server is elsewhere, --log for the board log
```

**Wi-Fi.** Store the network and the server's LAN address on the Passport once; it then reconnects on its own,
including on battery:

```bash
python3 tools/usb_link.py --wifi "My Wi-Fi" "password" --board-server 192.168.1.10:7300
```

`--show-config` prints the stored settings, `--clear-wifi` returns to USB only. The server accepts one board at a
time, so while the USB link is up the Wi-Fi link stands by instead of connecting.

### 5. Mirror, calibrate, run

Open `http://<computer-ip>:8890`, start **Control Center → Screen Mirroring → iPhone Agent**, run **Manual
controls → Calibrate**, then give the agent a task. Re-calibrate after changing `CONFIG_AGENT_MOVE_STEP`,
`CONFIG_AGENT_REPORT_INTERVAL_MS` or the AssistiveTouch tracking speed.

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
