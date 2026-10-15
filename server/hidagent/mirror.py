"""Turn Screen Mirroring back on with the board when it drops: Control Center -> Screen Mirroring -> the receiver.

With mirroring off there is no screen to look at, so the taps are learned once while it is live (a vision model
finds each one on the frame) and replayed blind afterwards. The phone connecting to the receiver again is the check
that it worked. Tapping the receiver while connected would stop mirroring, so a replay never does that.
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path

from .llm import LLM
from .phone import Phone
from .screen import frame_size

log = logging.getLogger(__name__)

POLL = 2             # seconds between checks of the mirror
GRACE = 5            # seconds mirroring must stay off before reconnecting (UxPlay rebuilds its pipeline in between)
BACKOFF = (0, 15, 45, 120)  # seconds before each attempt; after the last it gives up until mirroring is back
WAKE = 1.0           # seconds for the screen to light up after the pointer moves
STEP = 1.0           # seconds for Control Center or the receiver list to open when there is no mirror to watch
CONNECT_WAIT = 10    # seconds for mirroring to come up after tapping the receiver
POKE = 5             # seconds between pointer nudges while the phone mirrors but no frame has come yet
STALL = 30           # seconds of mirroring with no frame despite nudges before the receiver is restarted
FROZEN = 20          # seconds of board commands after the last frame before the video counts as stopped
CLOSE = (0.5, 0.05)  # top center: empty in Control Center and outside the receiver list; the status bar elsewhere

FIND_PROMPT = """You find one element on an iPhone screenshot (iOS Control Center, Chinese or English UI).
Reply with its center as two integer pixel coordinates of the screenshot: X Y
Reply none if it is not on the screen. Nothing else."""

PAGE = ("the topmost of the small page icons stacked along the right edge of Control Center (a heart: the first "
        "page, with the favorite controls). Reply none if there are no such page icons.")
BUTTON = ("the Screen Mirroring button (屏幕镜像): a round button whose icon is two overlapping rectangles. "
          "Not AirDrop (concentric arcs) and not the AirPlay icon of the media player.")
RECEIVER = "the row named \"{name}\" in the Screen Mirroring list: its name text."


class Mirror:
    def __init__(self, phone: Phone, screen, llm: LLM, file: Path, name: str, busy=lambda: False):
        self.phone, self.screen, self.llm, self._file, self.name = phone, screen, llm, file, name
        self.busy = busy  # True while an agent task runs: never reconnect then
        saved = json.loads(file.read_text()) if file.exists() else {}
        self.auto: bool = saved.get("auto", True)
        self.spots: dict | None = saved.get("spots")  # {"page": [x, y] | None, "button": [x, y], "receiver": [x, y]}
        self.state = "idle"  # idle | learning | reconnecting | waiting | gave up
        self.attempts = 0
        self.error = ""
        self._job: asyncio.Task | None = None

    @property
    def learned(self) -> bool:
        return bool(self.spots)

    @property
    def running(self) -> bool:
        return self._job is not None and not self._job.done()

    def status(self) -> dict:
        return {"auto": self.auto, "learned": self.learned, "state": self.state, "attempts": self.attempts,
                "error": self.error}

    def set_auto(self, on: bool) -> None:
        self.auto, self.attempts, self.error = on, 0, ""
        if self.state == "gave up":
            self.state = "idle"
        self._save()

    def _save(self) -> None:
        self._file.write_text(json.dumps({"auto": self.auto, "spots": self.spots}))

    async def stop(self) -> None:
        """Cancel a learn or reconnect in flight (emergency stop, or an agent task starting) and wait for it."""
        if self.running:
            self._job.cancel()
            await asyncio.wait({self._job})

    async def run(self, coro) -> bool:
        """Run a learn or reconnect as the one job on the phone; an emergency stop cancels it."""
        if self.running:
            coro.close()
            raise RuntimeError("already reconnecting or learning")
        if self.busy():
            coro.close()
            raise RuntimeError("an agent task is running")
        self._job = asyncio.create_task(coro)
        await asyncio.wait({self._job})
        if self._job.cancelled():
            self.state = "idle"
            raise RuntimeError("stopped")
        return self._job.result()

    # ---- learning (mirroring live) -----------------------------------------------------------------------------

    async def learn(self) -> dict:
        """Open Control Center and the receiver list on the live mirror and note where each tap goes."""
        if not self.screen.live:
            raise RuntimeError("no phone screen: start Screen Mirroring first, then learn")
        if not self.phone.calibrated:
            raise RuntimeError("pointer not calibrated")
        self.state, self.error = "learning", ""
        try:
            await self.phone.reset()
            await self.phone.control_center()
            await self._settled()
            page = await self._find(PAGE, needed=False)
            if page:
                await self.phone.tap(*page)
                await self._settled()
            button = await self._find(BUTTON)
            await self.phone.tap(*button)
            await self._settled()
            receiver = await self._find(RECEIVER.format(name=self.name))
            await self._close()
        except Exception as e:
            self.error = str(e)[:200]
            raise
        finally:
            self.state = "idle"
        self.spots = {"page": page, "button": button, "receiver": receiver}
        self.attempts = 0
        self._save()
        log.info("mirror: learned %s", self.spots)
        return self.spots

    async def _settled(self) -> None:
        await self.screen.settled(quiet=0.3, least=0.5, most=3)

    async def _find(self, what: str, needed: bool = True) -> list[float] | None:
        """Ask the vision model where `what` is on the current frame; screen fractions, rounded."""
        frame = self.screen.frame
        size = frame_size(frame)
        if not self.screen.live or not size:
            raise RuntimeError("lost the phone screen while learning")
        w, h = size
        reply = await self.llm.chat(FIND_PROMPT, f"Find {what}\nThe screenshot is {w}x{h} pixels.", frame,
                                    max_tokens=30)
        spot = parse_spot(reply, w, h)
        log.info("mirror: %s -> %r -> %s", what[:40], reply.strip()[:40], spot)
        if spot is None and needed:
            raise RuntimeError(f"could not find {what.split(':')[0]} on screen")
        return spot

    async def _close(self) -> None:
        """Tap outside: once closes the receiver list, once more closes Control Center."""
        for _ in range(2):
            await self.phone.tap(*CLOSE, aim=False)
            await asyncio.sleep(STEP)

    # ---- replay (mirroring off) --------------------------------------------------------------------------------

    async def reconnect(self) -> bool:
        """Replay the learned taps blind; True once mirroring is live again."""
        if not self.learned:
            raise RuntimeError("learn the mirroring taps first, while mirroring is on")
        self.state, self.error = "reconnecting", ""
        try:
            await self.phone.reset()  # let go of any button, start from the corner
            await self.phone.device.send("move 1 0")  # wake the screen: the phone may have locked
            await self.phone.device.send("move -1 0")
            await asyncio.sleep(WAKE)
            await self.phone.control_center()
            await asyncio.sleep(STEP)
            for key in ("page", "button", "receiver"):
                if self.screen.mirroring:  # back already: tapping the receiver now would stop it again
                    break
                if self.spots.get(key):
                    await self.phone.tap(*self.spots[key])  # aims by itself only if mirroring is back already
                    await asyncio.sleep(STEP)
            ok = await self._wait_mirroring(CONNECT_WAIT)
            await self._close()  # the list stays open after connecting, and after a miss too
        except Exception as e:
            self.error = str(e)[:200]
            raise
        finally:
            self.state = "idle"
        log.info("mirror: reconnect %s", "worked" if ok else "did not bring mirroring back")
        return ok

    async def _wait_mirroring(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while not self.screen.mirroring:
            if time.time() > deadline:
                return False
            await asyncio.sleep(0.25)
        return True

    async def watch(self) -> None:
        """Background loop: when mirroring has been off for a while, reconnect, backing off between attempts."""
        due = None  # when the next attempt may start; None while the phone mirrors
        poked = 0.0
        stalled = None  # since when the phone has been mirroring with no frame although the pointer was nudged
        while True:
            await asyncio.sleep(POLL)
            if self.screen.mirroring:
                if due is not None or self.attempts:
                    log.info("mirror: live")
                due, self.attempts = None, 0
                if self.state in ("waiting", "gave up"):
                    self.state = "idle"
                if self.screen.live:
                    stalled = None
                    if self.phone.device.last - self.screen.updated > FROZEN:
                        # seen once: the phone kept its control link to UxPlay but closed the video link, and the
                        # agent tapped a frozen frame for minutes
                        log.warning("mirror: commands for %ds and no new frame; restarting the receiver", FROZEN)
                        self.screen.restart()
                    continue
                if time.time() - poked > POKE:
                    poked = time.time()
                    if await self._poke():
                        stalled = stalled or time.time()
                    else:
                        stalled = None  # no nudge, no frame to expect
                if stalled and time.time() - stalled > STALL:
                    # seen once: the phone stayed connected but UxPlay sent no video until it was restarted
                    log.warning("mirror: connected but no video for %ds; restarting the receiver", STALL)
                    stalled = None
                    self.screen.restart()
                continue
            stalled = None
            if due is None:
                due = time.time() + GRACE
            if not (self.auto and self.learned and self.phone.calibrated and self.phone.device.connected):
                continue
            if self.running or self.busy() or self.state == "gave up":
                continue
            if self.attempts >= len(BACKOFF):
                self.state = "gave up"
                log.info("mirror: gave up after %d attempts; reconnect by hand or toggle auto", self.attempts)
                continue
            if time.time() < due:
                self.state = "waiting"
                continue
            self.attempts += 1
            log.info("mirror: off, reconnecting (attempt %d)", self.attempts)
            try:
                ok = await self.run(self.reconnect())
            except Exception as e:  # stopped, a dropped board link, ...
                log.info("mirror: reconnect failed: %s", e)
                ok = False
                if self._job.cancelled():  # emergency stop: leave the phone alone until asked again
                    self.state = "gave up"
                    continue
            if ok:
                due = None  # mirroring may flicker while UxPlay sets up: start over with the grace period
            elif self.attempts < len(BACKOFF):
                due = time.time() + BACKOFF[self.attempts]
                self.state = "waiting"

    async def _poke(self) -> bool:
        """Mirroring but no frame yet (a still screen sends none): move the pointer a pixel to get one.
        False when the phone is not to be touched now or the nudge did not reach it."""
        if self.running or self.busy() or not self.phone.device.connected:
            return False
        try:
            await self.phone.device.send("move 1 0")
            await self.phone.device.send("move -1 0")
        except Exception as e:  # the board link dropped: the next poll tries again
            log.info("mirror: could not nudge the pointer: %s", e)
            return False
        return True


def parse_spot(reply: str, w: int, h: int) -> list[float] | None:
    """"X Y" in screenshot pixels -> [x, y] screen fractions; None for "none" or anything off the screen."""
    numbers = re.findall(r"-?\d+(?:\.\d+)?", reply)
    if "none" in reply.lower() or len(numbers) < 2:
        return None
    x, y = float(numbers[0]), float(numbers[1])
    if not (0 <= x <= w and 0 <= y <= h):
        return None
    return [round(x / w, 4), round(y / h, 4)]
