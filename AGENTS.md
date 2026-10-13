# AGENTS.md

Guidance for AI coding agents working in this repository. Read the [README](README.md) for what the project does.

## Scope

This file covers the whole repository. Work under `hardware/ai_passport/` must also follow
[`hardware/ai_passport/AGENTS.md`](hardware/ai_passport/AGENTS.md), which takes precedence there (ESP-IDF setup, flashing
policy, BSP rules, firmware validation).

## Layout

```
server/                  agent, web UI, AirPlay capture and board link (Python 3.10+, FastAPI)
  hidagent/              the package started by `python -m hidagent`
  hidagent/static/       web UI (index.html, i18n.js holds the English and Chinese strings)
  tests/                 pytest suite
hardware/ai_passport/    AI Passport firmware (ESP-IDF 5.5.3, Bluetooth HID) and tools/usb_link.py
hardware/hid_bridge/     original ESP32-S3 USB HID firmware (Arduino), built by hardware/flash.sh
docs/images/             README images
```

## How the parts fit

- The server listens for one board on TCP `DEVICE_PORT` (default 7300) and serves the web UI on `PORT` (8890).
- Both boards speak the same line protocol: the board sends `hello <reset reason> <uptime ms>`, then answers each
  command (`ping`, `home`, `move`, `press`, `release`, `click`, `scroll`, `type`, `key`) with `ok` or `err <reason>`.
  The server is board-agnostic; keep it that way.
- The AI Passport reaches the server either through `hardware/ai_passport/tools/usb_link.py` (USB serial ↔ TCP; on the
  serial port protocol replies start with `@`, everything else is firmware log) or by itself over Wi-Fi.
- A protocol change touches `server/hidagent/device.py`, both firmwares and the protocol table in both READMEs.
  Prefer additive, backward-compatible changes.

## Checks

| Changed | Run | CI |
|---|---|---|
| `server/` | `cd server && pip install -e ".[dev]" && ruff check . && pytest -q` | Yes, Python 3.10 and 3.12 |
| `hardware/ai_passport/` | `cd hardware/ai_passport && ./tools/validate.sh` (ESP-IDF activated) | **No**, run it locally |
| `hardware/hid_bridge/` | `arduino-cli compile` as in `.github/workflows/ci.yml` | Yes |

Server code targets Python 3.10 (no newer syntax), line length 120, ruff rules `E F W I B UP`. Add or update tests
with behavior changes. Report what was run and what was not, separately for build, host tests and device tests.

## Setting up for a user

When asked to install and run the project (the README's quick-start prompt):

1. Flash the Passport with the latest release `…-full.bin` at `0x0` (first install; erases pairing and Wi-Fi
   settings) or `…-app.bin` at `0x10000` (update; keeps them). Ask before flashing.
2. Install UxPlay. Homebrew may not have it; then build it from <https://github.com/FDH2/UxPlay> (needs cmake,
   gstreamer, libplist) and put it on `PATH` for the server. The first start takes minutes while GStreamer scans
   plugins; do not treat that as a hang.
3. Create `server/.venv`, install `-e . pyserial`, write `server/.env`. The model must accept images: verify with a
   small image request before starting, and pick a vision-capable model if the provider has several.
4. Start the server, then `usb_link.py`. Opening the serial port restarts the Passport; the iPhone reconnects on its
   own within about 30 seconds.
5. Pairing (Settings → Bluetooth → AI Passport), AssistiveTouch, Screen Mirroring and calibration happen on the
   user's phone and web page: tell the user what to do and confirm with them.

## Safety

- Never commit `server/.env`, API keys, Wi-Fi passwords or traces under `~/.hidagent`. Keep secrets out of logs,
  commits, release notes and issue text.
- Ask before flashing a device, pushing, tagging, publishing a release, or renaming/deleting GitHub repositories.
- The agent acts on a real phone. Do not start agent tasks on the user's phone unless asked, and keep the
  emergency stop (web UI, <kbd>Esc</kbd>, or the Passport's OK button) available.

## Docs and naming

- Keep `README.md` and `README.zh-CN.md` in sync; `hardware/ai_passport` uses `README.zh_CN.md`.
- The product is **AI Passport iPhone Agent** and the board is **AI Passport**. Do not put the chip model in titles
  or taglines, and do not add prices. Store links come from the official site, <https://ai-passport.folotoy.cn/>.
- Credit the upstream project, [onehumanbeing/esp32-iphone-agent](https://github.com/onehumanbeing/esp32-iphone-agent).

## Git

- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, …),
  with a scope where it helps (`feat(server):`, `fix(ai_passport):`).
- `origin` is this fork, `upstream` is onehumanbeing/esp32-iphone-agent. Bring in upstream with
  `git fetch upstream && git merge upstream/main`; resolve README conflicts in favor of the AI Passport content.
- AI Passport firmware releases use tags `ai-passport-vX.Y.Z` and assets named
  `ai-passport-iphone-agent-vX.Y.Z-{full,app,bootloader,partition-table}.bin` plus `SHA256SUMS`, built from a clean
  checkout of the tag.
