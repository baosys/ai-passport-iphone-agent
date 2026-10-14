"""Remembered routines: a task the models finished once is replayed the next time without asking them.

Before each remembered tap or swipe, the replay checks that the spot it is about to press still looks the way it
did last time (a small patch of the screen around it, not the whole screen, so a new chat or badge elsewhere does
not matter). When it does not, the replay stops and the models take over from there. A run that finishes
replaces the routine with the path that worked; a routine that broke twice in a row is not replayed again until
the models finish the task once more.
"""

import io
import json
import logging
import re
from pathlib import Path

from PIL import Image, ImageChops

from .shortcuts import POLITE

log = logging.getLogger(__name__)

# the patch around a pointer target: a band as wide as half the screen and about a list row high (half sizes as a
# share of the screen width), kept at PATCH_PX pixels. A small square was too blurry: an empty area scored close
# to the "通用" row, and the General page's rows scored as the Settings root's.
PATCH = (0.25, 0.05)
PATCH_PX = (100, 20)
CHANGED = 40  # grey difference (0-255) at which a pixel counts as changed
SAME = 0.03  # share of changed pixels under which a patch still shows the same thing (measured: same screen 0.0,
# the nearest other screen 0.09)
MAX_FAILS = 2  # broken replays in a row before the routine waits for the models to redo it
# a task that asks something: its answer must be read off today's screen, not last time's
QUESTION = re.compile(r"[?？]|什么|多少|几[点个号时天条]|吗|哪|谁|怎么样|是否|有没有|查一下|查查|看看|告诉我|"
                      r"\b(?:what|how|which|who|when|where|whether|is there|are there|check|tell me)\b", re.I)


def normalize(task: str) -> str:
    """The task without politeness, punctuation, spaces and case, so "请打开设置。" matches "打开设置"."""
    task = re.sub(rf"^{POLITE}", "", task.strip(), flags=re.I)
    return re.sub(r"[\s\W_]+", "", task).lower()


def asks(task: str) -> bool:
    return bool(QUESTION.search(task))


def target(act: dict) -> tuple[float, float] | None:
    """Where on screen (fractions) a pointer action presses first, or None for keyboard actions."""
    if act["action"] in ("tap", "circle"):
        return act["x"] / 1000, act["y"] / 1000
    if act["action"] == "swipe":
        return act["x1"] / 1000, act["y1"] / 1000
    if act["action"] == "path":
        return act["points"][0][0] / 1000, act["points"][0][1] / 1000
    return None


def patch(jpeg: bytes, at: tuple[float, float]) -> list[int] | None:
    """A small grey patch of the screen around `at`."""
    try:
        img = Image.open(io.BytesIO(jpeg)).convert("L")
    except Exception:
        return None
    w, h = img.size
    rx, ry = PATCH[0] * w, PATCH[1] * w
    box = (round(at[0] * w - rx), round(at[1] * h - ry), round(at[0] * w + rx), round(at[1] * h + ry))
    box = (max(box[0], 0), max(box[1], 0), min(box[2], w), min(box[3], h))
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return list(img.crop(box).resize(PATCH_PX, Image.BILINEAR).tobytes())


def same(a: list[int] | None, b: list[int] | None) -> bool:
    if a is None or b is None or len(a) != len(b):
        return False
    diff = ImageChops.difference(Image.frombytes("L", PATCH_PX, bytes(a)), Image.frombytes("L", PATCH_PX, bytes(b)))
    return sum(d >= CHANGED for d in diff.tobytes()) / len(a) < SAME


class Routines:
    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, dict] = {}
        if path and path.exists():
            try:
                self.data = json.loads(path.read_text())
            except (OSError, ValueError) as e:
                log.warning("could not read %s: %s", path, e)

    def find(self, task: str) -> dict | None:
        routine = self.data.get(normalize(task))
        return routine if routine and routine["fails"] < MAX_FAILS else None

    def learn(self, task: str, steps: list[dict], answer: str) -> None:
        """Remember the steps (actions with "cue" patches for the ones that were checked on screen)."""
        if not steps:
            return
        old = self.data.get(normalize(task), {})
        self.data[normalize(task)] = {"task": task, "steps": steps, "answer": answer,
                                      "ok": old.get("ok", 0) + 1, "fails": 0}
        self._save()
        log.info("routines: learned %r (%d steps)", task, len(steps))

    def replayed(self, task: str) -> None:
        if routine := self.data.get(normalize(task)):
            routine["ok"] += 1
            routine["fails"] = 0
            self._save()

    def broke(self, task: str) -> None:
        if routine := self.data.get(normalize(task)):
            routine["fails"] += 1
            self._save()

    def forget(self, task: str) -> bool:
        found = self.data.pop(normalize(task), None) is not None
        self._save()
        return found

    def _save(self) -> None:
        if self.path:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False))
