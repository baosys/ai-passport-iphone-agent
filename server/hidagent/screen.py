"""The phone's screen, received over AirPlay mirroring by UxPlay, as JPEG frames.

UxPlay runs detached from the server and serves its frames on a local TCP port, so it outlives a server restart and
the phone keeps mirroring; the next server start finds it again through a small state file.
"""

import asyncio
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

RETRY = 0.5  # seconds between attempts to reach the frame port (it is closed while no phone is mirroring)


def frame_size(jpeg: bytes | None) -> tuple[int, int] | None:
    """(width, height) in pixels, read from the JPEG header."""
    f = jpeg or b""
    i = f.find(b"\xff\xc0")  # baseline start-of-frame: ... height(2) width(2)
    if i < 0 or len(f) < i + 9:
        return None
    w, h = int.from_bytes(f[i + 7:i + 9], "big"), int.from_bytes(f[i + 5:i + 7], "big")
    return (w, h) if w and h else None


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _command(pid: int) -> str:
    try:
        return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _remote(peer: str) -> bool:
    """True for a connection to another machine (the phone), not to this one."""
    return not (peer.startswith("127.") or peer.startswith("[::1]") or peer.startswith("[::ffff:127."))


class Screen:
    """Keeps a UxPlay receiver running (GStreamer sink: JPEG frames on a local TCP port) and keeps the latest frame."""

    def __init__(self, name: str = "iPhone Agent", fps: int = 8, quality: int = 75, state: Path | None = None):
        self.name, self.fps, self.quality = name, fps, quality
        self._state = state  # where the running receiver is noted, so the next server start reuses it
        self.frame: bytes | None = None
        self.updated = 0.0
        self.pid: int | None = None
        self._proc = None  # set only when this server started the receiver (an adopted one is not our child)
        self._sock = None
        self._clients_at, self._clients_n = 0.0, None
        self._stopping = False

    def start(self) -> None:
        threading.Thread(target=self._supervise, daemon=True).start()

    def stop(self) -> None:
        """Stop reading frames. The receiver keeps running, so the phone keeps mirroring across a restart."""
        self._stopping = True
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def restart(self) -> None:
        """Stop the receiver; the supervisor starts a fresh one (the phone then has to connect again)."""
        if not self._running():
            return
        if self._proc is not None:
            self._proc.terminate()
        else:
            os.kill(self.pid, signal.SIGTERM)

    def _config(self) -> dict:
        return {"name": self.name, "fps": self.fps, "quality": self.quality}

    def _running(self) -> bool:
        if self._proc is not None:
            return self._proc.poll() is None  # also reaps it once it exits
        return _alive(self.pid)

    def _supervise(self) -> None:
        """Keep a receiver running and read its frames. UxPlay rebuilds its pipeline (and the frame port) for every
        mirroring session, so a closed or refused port is normal; only a receiver that exited is started again."""
        port = self._adopt()
        while not self._stopping:
            if not self._running():
                if self.pid:
                    log.warning("AirPlay receiver exited, restarting")
                    time.sleep(1)
                try:
                    port = self._spawn()
                except OSError as e:  # e.g. uxplay is not on PATH
                    log.error("cannot start the AirPlay receiver: %s", e)
                    time.sleep(10)
                    continue
            try:
                self._sock = socket.create_connection(("127.0.0.1", port), timeout=2)
            except OSError:
                time.sleep(RETRY)
                continue
            log.debug("reading frames from the AirPlay receiver on port %d", port)
            self._read(self._sock)  # returns when the pipeline closes the port: the phone stopped mirroring
            self._sock = None
            self.frame = None

    def _adopt(self) -> int | None:
        """Reuse a receiver a previous server run left behind, if it is still running with the same settings."""
        if self._state is None or not self._state.exists():
            return None
        try:
            saved = json.loads(self._state.read_text())
            pid, port = int(saved["pid"]), int(saved["port"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not _alive(pid) or f"port={port}" not in _command(pid):  # gone, or the pid now belongs to something else
            return None
        if saved.get("config") != self._config():
            log.info("AirPlay receiver (pid %d) has other settings; replacing it", pid)
            os.kill(pid, signal.SIGTERM)
            return None
        self.pid = pid
        log.info("AirPlay receiver '%s' already running (pid %d): reusing it", self.name, pid)
        return port

    def _spawn(self) -> int:
        port = _free_port()
        sink = (f"videorate drop-only=true ! video/x-raw,framerate={self.fps}/1 ! "
                f"jpegenc quality={self.quality} ! tcpserversink host=127.0.0.1 port={port} sync=false")
        logfile = self._state.with_name("uxplay.log") if self._state else None
        if logfile and logfile.exists():
            logfile.replace(logfile.with_name("uxplay.log.1"))  # keep the last run's log: why did it exit?
        out = open(logfile, "wb") if logfile else None  # the receiver keeps its own copy of the descriptor
        try:
            # its own session: Ctrl+C on the server or closing its terminal does not reach the receiver
            self._proc = subprocess.Popen(
                ["uxplay", "-n", self.name, "-nh", "-a", "-reset", "0", "-vs", sink],
                stdin=subprocess.DEVNULL, stdout=out or sys.stdout, stderr=subprocess.STDOUT, start_new_session=True)
        finally:
            if out:
                out.close()
        self.pid = self._proc.pid
        if self._state:
            self._state.write_text(json.dumps({"pid": self.pid, "port": port, "config": self._config()}))
        log.info("AirPlay receiver '%s' started (pid %d, log %s)", self.name, self.pid, logfile or "stdout")
        return port

    @property
    def mirroring(self) -> bool:
        """A phone is mirroring to the receiver, frame or not: a still screen sends none, so right after the server
        restarts (or the frame port reopens) there is no frame until something on the phone moves."""
        if not self._running():
            return False
        clients = self._clients()
        if clients is not None:  # is a phone connected to UxPlay right now?
            return clients > 0
        return self.frame is not None and time.time() - self.updated < 90  # unknown: guess from recent frames

    @property
    def live(self) -> bool:
        """Mirroring, with a frame to look at."""
        return self.frame is not None and self.mirroring

    def _clients(self) -> int | None:
        """Established TCP connections from another machine to UxPlay (a mirroring phone keeps some open).

        Read from /proc on Linux and with lsof elsewhere, cached for a second; None when neither works.
        """
        if time.time() - self._clients_at < 1:
            return self._clients_n
        n = self._clients_proc() if os.path.isdir("/proc/self/fd") else self._clients_lsof()
        self._clients_at, self._clients_n = time.time(), n
        return n

    def _clients_proc(self) -> int | None:
        try:
            inodes = set()
            for fd in os.listdir(f"/proc/{self.pid}/fd"):
                link = os.readlink(f"/proc/{self.pid}/fd/{fd}")
                if link.startswith("socket:["):
                    inodes.add(link[8:-1])
            n = 0
            for table in ("/proc/net/tcp", "/proc/net/tcp6"):
                with open(table) as f:
                    for row in f.readlines()[1:]:
                        cols = row.split()
                        # 01 = ESTABLISHED; the remote address is hex, 0100007F = 127.0.0.1 (our frame reader)
                        n += cols[3] == "01" and cols[9] in inodes and not cols[2].startswith(
                            ("0100007F", "0000000000000000FFFF00000100007F", "00000000000000000000000001000000"))
        except OSError:
            return None
        return n

    def _clients_lsof(self) -> int | None:
        try:
            out = subprocess.run(["lsof", "-a", "-p", str(self.pid), "-iTCP", "-sTCP:ESTABLISHED", "-nP", "-Fn"],
                                 capture_output=True, text=True, timeout=2).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        if not out.startswith("p"):  # no such process, or no connections at all
            return 0 if _alive(self.pid) else None
        return sum(_remote(line.partition("->")[2]) for line in out.splitlines() if line.startswith("n"))

    def local_ip(self) -> str | None:
        """This computer's IPv4 address as the phone reaches it: the local end of the mirroring connection.

        Unlike the default route, it stays right with a VPN up (its tunnel address is no use to the phone)."""
        if not self.pid:
            return None
        try:
            out = subprocess.run(["lsof", "-a", "-p", str(self.pid), "-iTCP", "-sTCP:ESTABLISHED", "-nP", "-Fn"],
                                 capture_output=True, text=True, timeout=2).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        for line in out.splitlines():
            local, _, peer = line[1:].partition("->")
            if not (line.startswith("n") and _remote(peer)):
                continue
            host = local.rpartition(":")[0]
            if not host.startswith("["):
                return host
            # IPv6 link-local, as lsof writes it on macOS: [fe80:<interface index in hex>::...]. A URL typed in
            # Safari cannot carry the zone, so take the IPv4 address of the same interface.
            zone = host[1:].split(":")[1] if host.startswith("[fe80:") else ""
            try:
                name = socket.if_indextoname(int(zone, 16))
                out = subprocess.run(["ifconfig", name], capture_output=True, text=True, timeout=2).stdout
            except (ValueError, OSError, subprocess.SubprocessError):
                continue
            for word in out.split("inet ")[1:]:
                return word.split()[0]
        return None

    @property
    def aspect(self) -> float:
        """Frame width / height (portrait iPhone as a fallback)."""
        size = frame_size(self.frame)
        return size[0] / size[1] if size else 9 / 19.5

    async def settled(self, quiet: float = 0.35, least: float = 0.25, most: float = 4) -> None:
        """Wait until the screen stops changing (AirPlay only sends frames on change)."""
        started = time.time()
        await asyncio.sleep(least)
        while time.time() - self.updated < quiet and time.time() - started < most:
            await asyncio.sleep(0.1)

    async def fresh(self, since: float, timeout: float = 0.8) -> bytes | None:
        """The first frame received after `since`, or None if none arrives in time."""
        deadline = time.time() + timeout
        while self.updated <= since:
            if time.time() > deadline:
                return None
            await asyncio.sleep(0.02)
        return self.frame

    def _read(self, sock: socket.socket) -> None:
        sock.settimeout(None)
        buf = b""
        try:
            while chunk := sock.recv(1 << 16):
                buf += chunk
                while (start := buf.find(b"\xff\xd8")) >= 0 and (end := buf.find(b"\xff\xd9", start)) >= 0:
                    self.frame, self.updated = buf[start:end + 2], time.time()
                    buf = buf[end + 2:]
        except OSError:
            pass
        finally:
            sock.close()
