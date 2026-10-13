#!/usr/bin/env python3
"""End-to-end test of tools/usb_link.py: a pseudo-terminal plays the Passport firmware
(log lines mixed with "@" replies) and a TCP listener plays the agent server's board port."""

from __future__ import annotations

import importlib.util
import os
import socket
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    import serial  # noqa: F401
except ImportError:  # pragma: no cover - depends on the host
    serial = None


def load_usb_link():
    spec = importlib.util.spec_from_file_location("usb_link", ROOT / "tools" / "usb_link.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeFirmware(threading.Thread):
    """Answers like agent_link_usb.c: '@'-prefixed replies, with log noise in between."""

    def __init__(self, fd: int):
        super().__init__(daemon=True)
        self.fd, self.seen = fd, []

    def run(self) -> None:
        buf = b""
        while True:
            try:
                chunk = os.read(self.fd, 256)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode().strip()
                self.seen.append(line)
                if line == "hello":
                    reply = "hello power-on 1234"
                elif line == "bad":
                    reply = "err unknown command"
                else:
                    reply = "ok"
                os.write(self.fd, b"I (99) agent_hid: some log line\r\n@" + reply.encode() + b"\r\n")


@unittest.skipIf(serial is None, "pyserial not installed")
class UsbLinkTest(unittest.TestCase):
    def test_relays_commands_and_replies(self) -> None:
        usb_link = load_usb_link()
        master, slave = os.openpty()
        firmware = FakeFirmware(master)
        firmware.start()

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        port = listener.getsockname()[1]

        board = usb_link.Board(os.ttyname(slave), show_log=False)
        relay = threading.Thread(target=usb_link.relay, args=(board, ("127.0.0.1", port)), daemon=True)
        relay.start()

        conn, _ = listener.accept()
        conn.settimeout(5)
        server = conn.makefile("rwb")
        # The greeting arrives first, as from the Wi-Fi firmware.
        self.assertEqual(server.readline().decode().strip(), "hello power-on 1234")
        for command, expected in (("ping", "ok"), ("move 10 -5", "ok"), ("bad", "err unknown command")):
            server.write(command.encode() + b"\n")
            server.flush()
            self.assertEqual(server.readline().decode().strip(), expected)

        # The server hanging up makes the bridge release the board's buttons.
        server.close()
        conn.close()
        relay.join(5)
        self.assertFalse(relay.is_alive())
        self.assertEqual(firmware.seen, ["hello", "ping", "move 10 -5", "bad", "bye"])

        board.close()
        listener.close()
        os.close(slave)
        os.close(master)

    def test_host_port_parsing(self) -> None:
        usb_link = load_usb_link()
        self.assertEqual(usb_link.host_port("192.168.1.10:7400", 7300), ("192.168.1.10", 7400))
        self.assertEqual(usb_link.host_port("agent.local", 7300), ("agent.local", 7300))


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    unittest.main()
