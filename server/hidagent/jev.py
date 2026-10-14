"""Jev (TypeSafe System One): quick typed judgments where plain code would need to understand words or a screen.

The vision models plan and act. Jev answers the small questions around them in about half a second each:
whether a step of the task is just "open an app", "go home" or "go back"; which remembered routine a reworded
task means; whether the task asks for an answer; whether the screen still shows the page a remembered step ran on;
when a remembered button is no longer where it was, where it is now; and whether the task looks done. The agent
acts on an answer only when Jev is sure (or, for a page or a finished task, clearly says no), and checks a found
button against its remembered pixels before pressing it. Without TYPESAFE_API_KEY, or when Jev is unsure or
unreachable, the agent keeps to its regex rules, pixel checks and models.
"""

import base64
import io
import logging
import os
import uuid
from dataclasses import dataclass

import httpx
from PIL import Image

log = logging.getLogger(__name__)

URL = "https://api.typesafe.ai/v1/systemone"
SURE = 0.8  # probability under which an answer is not acted on
NO = 0.2  # yes/no probability under which a page or a finished task is clearly not there
QUICK = 3.0  # timeout (seconds) of the checks made between replayed steps, which must not hold them up for long
MAX_CLAUSES = 4  # task steps asked about, from the start; the rest goes to the models anyway
MAX_ROUTINES = 100  # remembered tasks offered as matches, most used first
IMAGE_SIDES = (256, 1568)  # the API's smallest and largest image side, in pixels
KINDS = {
    "open": "Open (launch) one app by its name, and nothing more: no page, contact, search or action inside it.",
    "home": "Go to the home screen.",
    "back": "Go back one page or screen.",
    "other": "Anything else, or open an app and also do something in it.",
}


@dataclass
class Triage:
    kinds: list[str]  # for each of the task's first clauses: a key of KINDS ("other" when Jev is unsure)
    routine: str | None  # the remembered task that asks for the same thing, if Jev is sure
    asks: bool  # the task wants information read off the screen


def _image(jpeg: bytes) -> bytes:
    """The frame within the API's size limits, as JPEG."""
    img = Image.open(io.BytesIO(jpeg))
    small, large = IMAGE_SIDES
    if small <= min(img.size) and max(img.size) <= large:
        return jpeg
    scale = small / min(img.size) if min(img.size) < small else large / max(img.size)
    img = img.convert("RGB").resize((max(round(img.width * scale), small), max(round(img.height * scale), small)))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


def _noul(answers: dict | None, key: str) -> float | None:
    try:
        return float(answers[key]["noul"])
    except (KeyError, TypeError, ValueError):
        return None


class Jev:
    def __init__(self, key: str | None, model: str = "jev-latest", vision_model: str = "jev-preview",
                 timeout: float = 8.0, transport: httpx.AsyncBaseTransport | None = None):
        self.key, self.model, self.vision_model, self.timeout = key, model, vision_model, timeout
        self.transport = transport

    @classmethod
    def from_env(cls) -> "Jev":
        return cls(os.environ.get("TYPESAFE_API_KEY") or None, os.environ.get("JEV_MODEL", "jev-latest"),
                   os.environ.get("JEV_VISION_MODEL", "jev-preview"))

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    async def ask(self, state, questions: dict, images: dict[str, bytes] | None = None,
                  timeout: float | None = None) -> dict | None:
        """The answers keyed like `questions`, or None when Jev is off or the request failed.

        `images` maps a reference written somewhere in `state` or `questions` to a JPEG.
        """
        if not self.key:
            return None
        body = {"model": self.vision_model if images else self.model, "state": state, "questions": questions}
        headers = {"Authorization": f"Bearer {self.key}"}
        try:
            if images:
                body["assets"] = {ref: {"type": "image", "format": "jpg",
                                        "data_base64": base64.b64encode(_image(jpeg)).decode()}
                                  for ref, jpeg in images.items()}
                headers["TypeSafe-Danger-Zone-Images"] = "true"
            async with httpx.AsyncClient(timeout=timeout or self.timeout, transport=self.transport) as client:
                reply = await client.post(URL, json=body, headers=headers)
                reply.raise_for_status()
                return reply.json()["answers"]
        except (httpx.HTTPError, KeyError, ValueError, OSError) as e:  # OSError: a frame PIL cannot read
            log.warning("jev: %s", e)
            return None

    async def triage(self, task: str, clauses: list[str], remembered: list[str]) -> Triage | None:
        """What the task asks for, in one request; None when Jev could not answer."""
        clauses = clauses[:MAX_CLAUSES]
        questions = {f"c{i}": {"type": "choice", "criteria": KINDS, "instructions": (
            f"`request` is a request to an iPhone, split into the steps in `clauses`. What does step "
            f"`clauses[{i}]` alone ask the phone to do?")} for i in range(len(clauses))}
        questions["asks"] = {"type": "noul", "instructions": (
            "Does `request` ask for information that has to be read off the phone's screen and reported back, "
            "rather than only asking for something to be done?")}
        routines = remembered[:MAX_ROUTINES]
        if routines:
            questions["routine"] = {"type": "choice", "instructions": (
                "Which earlier request asks for exactly the same thing as `request` (same app, same page, same "
                "contact, same text), so that the steps that did it then would do `request` now?"),
                "criteria": {**{f"r{i}": text for i, text in enumerate(routines)},
                             "none": "None of them: `request` asks for something different."}}
        answers = await self.ask({"request": task, "clauses": clauses}, questions)
        if answers is None:
            return None
        kinds = []
        for i in range(len(clauses)):
            answer = answers[f"c{i}"]
            kinds.append(answer["choice"] if answer["probabilities"][answer["choice"]] >= SURE else "other")
        routine = None
        if answer := answers.get("routine"):
            pick = answer["choice"]
            if pick != "none" and answer["probabilities"][pick] >= SURE:
                routine = routines[int(pick[1:])]
        log.info("jev: %r -> %s, routine %r, asks %.2f", task, kinds, routine, answers["asks"]["noul"])
        return Triage(kinds, routine, answers["asks"]["noul"] >= 0.5)

    async def find(self, screen: bytes, look: bytes) -> tuple[float, float, float, float] | None:
        """Where on `screen` (left, top, right, bottom, as fractions) the control in the middle of `look` is.

        None when Jev does not see it there.
        """
        shot, sample = uuid.uuid4().hex, uuid.uuid4().hex
        answers = await self.ask({"screen": shot}, {
            "there": {"type": "noul", "instructions": (
                f"Last time the button or row in the middle of this picture was pressed: {sample}. Is that same "
                "button or row (same label, same icon) on `screen` now?")},
            "where": {"type": "box", "instructions": (
                f"the button or row on `screen` that is the one in the middle of this picture: {sample}")},
        }, images={shot: screen, sample: look})
        if answers is None or answers["there"]["noul"] < SURE:
            return None
        box = answers["where"]["box"]
        return box["left"], box["top"], box["right"], box["bottom"]

    async def page(self, screen: bytes, page: str) -> float | None:
        """How likely `screen` shows the page described by `page` (e.g. "Settings > General"); None without Jev."""
        shot = uuid.uuid4().hex
        answers = await self.ask({"screen": shot, "page": page}, {"there": {"type": "noul", "instructions": (
            "Is the iPhone screen `screen` showing the page described by `page`: the same app and the same page "
            "or screen in it, not a page next to it or a sheet or alert on top of it?")}},
            images={shot: screen}, timeout=QUICK)
        if (p := _noul(answers, "there")) is not None:
            log.info("jev: page %r: %.2f", page, p)
        return p

    async def done(self, screen: bytes, task: str) -> float | None:
        """How likely `task` has been fully done, judging by `screen` after the last step; None without Jev."""
        shot = uuid.uuid4().hex
        answers = await self.ask({"task": task, "screen": shot}, {"done": {"type": "noul", "instructions": (
            "`task` was asked of an iPhone and `screen` is the phone's screen after the last step. Has `task` been "
            "fully done, every part of it (e.g. a message not only typed but sent, a setting not only found but "
            "changed), judging by `screen`?")}}, images={shot: screen}, timeout=QUICK)
        if (p := _noul(answers, "done")) is not None:
            log.info("jev: done %r: %.2f", task, p)
        return p
