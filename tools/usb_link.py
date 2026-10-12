#!/usr/bin/env python3
"""Bridge an AI Passport plugged in over USB to the iPhone agent server.

The agent server (https://github.com/onehumanbeing/esp32-iphone-agent, `python -m hidagent`) waits for its
board on a TCP port (DEVICE_PORT, default 7300). AI Passport can join it over Wi-Fi like the reference board,
or sit on the computer's USB port; this script is the USB path. It connects to the server as if it were the
board and relays each command line to the Passport's USB serial port, and each reply back.

The serial port also carries the firmware log. Protocol replies start with "@"; everything else is log
output, printed with --log.

    python3 tools/usb_link.py                         # bridge (auto-detects the port, server on localhost:7300)
    python3 tools/usb_link.py --server 192.168.1.10:7300 --log
    python3 tools/usb_link.py --wifi "My Wi-Fi" secret --board-server 192.168.1.10:7300
                                                      # store Wi-Fi settings on the Passport instead and reboot it
    python3 tools/usb_link.py --show-config | --forget-phone

Needs pyserial (`pip install pyserial`).
"""

from __future__ import annotations

import argparse
import queue
import socket
import sys
import threading
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - guidance only
    sys.exit("pyserial is required: pip install pyserial")

ESPRESSIF_VID = 0x303A
REPLY_TIMEOUT = 30.0  # longest board command (a long move or typed text) is a few seconds


class BoardError(Exception):
    """The Passport was unplugged or stopped answering (kept apart from server-side OSErrors)."""


class Board:
    """The Passport's USB serial port: send a line, get the next "@" reply."""

    def __init__(self, port: str, show_log: bool):
        self.port, self.show_log = port, show_log
        self.replies: queue.Queue[str] = queue.Queue()
        self.alive = True
        self.ser = serial.Serial()
        self.ser.port, self.ser.baudrate, self.ser.timeout = port, 115200, 0.2
        # Opening the port must not toggle the C3's reset/boot lines (same as idf.py monitor).
        self.ser.dtr = False
        self.ser.rts = False
        self.ser.open()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        buf = b""
        try:
            while self.alive:
                buf += self.ser.read(256)
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode(errors="replace").rstrip("\r")
                    if line.startswith("@"):
                        self.replies.put(line[1:])
                    elif self.show_log and line:
                        print(f"board | {line}", file=sys.stderr)
        except (OSError, serial.SerialException) as e:
            if self.alive:  # not our own close()
                print(f"usb: {self.port} closed ({e})", file=sys.stderr)
        self.alive = False

    def ask(self, line: str, timeout: float = REPLY_TIMEOUT) -> str:
        if not self.alive:
            raise BoardError("board unplugged")
        try:
            self.ser.write(line.encode() + b"\n")
        except (OSError, serial.SerialException) as e:
            raise BoardError(f"write failed: {e}") from e
        try:
            return self.replies.get(timeout=timeout)
        except queue.Empty:
            raise BoardError(f"no reply to {line[:30]!r}") from None

    def drain(self) -> None:
        while not self.replies.empty():
            self.replies.get_nowait()

    def close(self) -> None:
        self.alive = False
        self.ser.close()


def find_port() -> str:
    ports = [p for p in list_ports.comports() if p.vid == ESPRESSIF_VID]
    if not ports:
        sys.exit("no AI Passport found on USB; plug it in with a data cable or pass --port")
    if len(ports) > 1:
        names = ", ".join(p.device for p in ports)
        sys.exit(f"several Espressif boards found ({names}); pick one with --port")
    return ports[0].device


def host_port(text: str, default_port: int) -> tuple[str, int]:
    host, _, port = text.rpartition(":") if ":" in text else (text, "", "")
    return host, int(port) if port else default_port


def relay(board: Board, server: tuple[str, int]) -> None:
    """One server session: greet, then relay command / reply pairs until either side goes away."""
    # The server reads the greeting within 1 s of accepting, so ask the board first:
    # "hello <reset reason> <uptime ms>", the same line the Wi-Fi board sends.
    board.drain()
    hello = board.ask("hello", timeout=3)
    with socket.create_connection(server, timeout=5) as sock:
        sock.settimeout(None)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.sendall(hello.encode() + b"\n")
        print(f"linked: board on {board.port} <-> server {server[0]}:{server[1]}", file=sys.stderr)
        reader = sock.makefile("rb")
        try:
            for raw in reader:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                reply = board.ask(line)
                sock.sendall(reply.encode() + b"\n")
        finally:
            if board.alive:
                try:
                    board.ask("bye", timeout=2)  # let go of any held button, like a dropped Wi-Fi link
                except BoardError:
                    pass


def run_bridge(args) -> None:
    server = host_port(args.server, 7300)
    while True:
        try:
            board = Board(args.port or find_port(), args.log)
        except (OSError, serial.SerialException) as e:
            print(f"usb: {e}; retrying", file=sys.stderr)
            time.sleep(2)
            continue
        waiting = False
        while board.alive:
            try:
                relay(board, server)
                print("server closed the link; reconnecting", file=sys.stderr)
                waiting = False
            except BoardError as e:
                print(f"board: {e}; reconnecting", file=sys.stderr)
                time.sleep(1)
            except OSError as e:  # server not up yet, or the link dropped
                if not waiting:
                    print(f"waiting for the agent server at {server[0]}:{server[1]} ({e})", file=sys.stderr)
                    waiting = True
                time.sleep(1)
        board.close()
        time.sleep(1)


def run_admin(args) -> None:
    board = Board(args.port or find_port(), args.log)
    time.sleep(0.3)
    board.drain()
    if args.wifi:
        ssid, password = args.wifi
        host, port = host_port(args.board_server, 7300)
        for key, value in (("ssid", ssid), ("pass", password), ("host", host), ("port", str(port))):
            reply = board.ask(f"set {key} {value}", timeout=3)
            if reply != "ok":
                sys.exit(f"set {key}: {reply}")
        board.ask("reboot", timeout=3)
        print(f"saved; AI Passport restarts and joins {ssid!r}, then connects to {host}:{port}")
    elif args.clear_wifi:
        board.ask("set ssid ", timeout=3)
        board.ask("reboot", timeout=3)
        print("Wi-Fi cleared; AI Passport restarts in USB-only mode")
    elif args.forget_phone:
        print(board.ask("forget", timeout=3))
        print("now also tap (i) > Forget This Device on the iPhone, then pair again")
    else:
        print(board.ask("config", timeout=3))
    board.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", help="serial port (default: the only Espressif USB device)")
    parser.add_argument("--server", default="127.0.0.1:7300", help="agent server board port (host:port)")
    parser.add_argument("--log", action="store_true", help="print the board's log lines")
    admin = parser.add_mutually_exclusive_group()
    admin.add_argument("--wifi", nargs=2, metavar=("SSID", "PASSWORD"), help="store Wi-Fi settings and reboot")
    admin.add_argument("--clear-wifi", action="store_true", help="forget Wi-Fi settings (USB only)")
    admin.add_argument("--show-config", action="store_true", help="print the stored settings")
    admin.add_argument("--forget-phone", action="store_true", help="delete the Bluetooth pairing")
    parser.add_argument("--board-server", default="", help="with --wifi: server address the board connects to")
    args = parser.parse_args()
    if args.wifi and not args.board_server:
        parser.error("--wifi needs --board-server HOST[:PORT] (this computer's LAN address)")
    try:
        if args.wifi or args.clear_wifi or args.show_config or args.forget_phone:
            run_admin(args)
        else:
            run_bridge(args)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
